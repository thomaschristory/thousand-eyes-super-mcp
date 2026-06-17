"""Tests for debug-mode capture (#8): env precedence, redaction, capture modes,
the RequestError path, and the resolve_debug_config None-default invariant."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from thousand_eyes_mcp.auth import ThousandEyesAuth
from thousand_eyes_mcp.config import DebugConfig, PaginationConfig, RetryConfig, load_config
from thousand_eyes_mcp.dispatcher import (
    Dispatcher,
    _redact_headers,
    _redact_url,
    _redact_value,
)
from thousand_eyes_mcp.loader import SpecLoader
from thousand_eyes_mcp.server import resolve_debug_config

# ---------------------------------------------------------------------------
# config: env precedence
# ---------------------------------------------------------------------------


def test_debug_defaults_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "THOUSANDEYES_MCP_DEBUG",
        "THOUSANDEYES_MCP_DEBUG_REDACT",
        "THOUSANDEYES_MCP_DEBUG_CAPTURE",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config(str(tmp_path / "absent.yaml"))
    assert cfg.debug.enabled is False
    assert cfg.debug.redact is True
    assert cfg.debug.capture == "errors"


def test_debug_env_enables_and_sets_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOUSANDEYES_MCP_DEBUG", "1")
    monkeypatch.setenv("THOUSANDEYES_MCP_DEBUG_CAPTURE", "all")
    cfg = load_config(str(tmp_path / "absent.yaml"))
    assert cfg.debug.enabled is True
    assert cfg.debug.capture == "all"


@pytest.mark.parametrize("value", ["0", "false", "False"])
def test_debug_env_falsey_disables(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A present-but-falsy env value disables, overriding a YAML enable."""
    monkeypatch.setenv("THOUSANDEYES_MCP_DEBUG", value)
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("debug:\n  enabled: true\n")
    cfg = load_config(str(cfg_file))
    assert cfg.debug.enabled is False


def test_debug_env_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOUSANDEYES_MCP_DEBUG", "true")
    monkeypatch.setenv("THOUSANDEYES_MCP_DEBUG_REDACT", "false")
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("debug:\n  enabled: false\n  redact: true\n")
    cfg = load_config(str(cfg_file))
    assert cfg.debug.enabled is True
    assert cfg.debug.redact is False


# ---------------------------------------------------------------------------
# resolve_debug_config: CLI > env/YAML, None-default invariant
# ---------------------------------------------------------------------------


def test_resolve_none_defaults_preserve_base() -> None:
    base = DebugConfig(enabled=True, redact=False, capture="all")
    out = resolve_debug_config(base, debug=None, all_calls=None, no_redact=None)
    assert out == base


def test_resolve_cli_debug_enables() -> None:
    base = DebugConfig(enabled=False)
    assert resolve_debug_config(base, debug=True).enabled is True


def test_resolve_all_calls_implies_enabled_and_capture_all() -> None:
    out = resolve_debug_config(DebugConfig(enabled=False), all_calls=True)
    assert out.enabled is True
    assert out.capture == "all"


def test_resolve_no_redact_turns_off_redaction() -> None:
    out = resolve_debug_config(DebugConfig(enabled=True, redact=True), no_redact=True)
    assert out.redact is False


# ---------------------------------------------------------------------------
# dispatcher capture
# ---------------------------------------------------------------------------


def _dispatcher(specs_dir: Path, debug: DebugConfig) -> tuple[Dispatcher, str]:
    index = SpecLoader(str(specs_dir), "7.0.88", read_write=False).load()
    action = next(name for name in index.by_action_name if name.startswith("get_tests"))
    d = Dispatcher(
        base_url="https://api.thousandeyes.com/v7",
        auth=ThousandEyesAuth(bearer_token="super-secret-bearer"),
        retry=RetryConfig(max_attempts=1),
        pagination=PaginationConfig(enabled=False),
        debug=debug,
    )
    d.set_index(index)
    return d, action


@pytest.mark.asyncio
@respx.mock
async def test_no_debug_attached_when_disabled(minimal_specs_dir: Path) -> None:
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(500, json={"errorCode": "BOOM"})
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=False))
    await d.connect()
    try:
        result = await d.call(action, {})
    finally:
        await d.close()
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert "debug" not in result


@pytest.mark.asyncio
@respx.mock
async def test_error_capture_includes_request_and_response(minimal_specs_dir: Path) -> None:
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(
            403, json={"errorCode": "FORBIDDEN"}, headers={"Set-Cookie": "sess=topsecret"}
        )
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=True))
    await d.connect()
    try:
        result = await d.call(action, {}, tool_name="tests")
    finally:
        await d.close()
    assert isinstance(result, dict)
    dbg = result["debug"]
    assert dbg["tool"] == "tests"
    assert dbg["action"] == action
    assert dbg["request"]["method"] == "GET"
    assert dbg["request"]["path"] == "/tests"
    assert dbg["response"]["status_code"] == 403
    assert dbg["response"]["error_code"] == "FORBIDDEN"
    # Authorization request header and Set-Cookie response header are redacted.
    assert dbg["request"]["headers"]["Authorization"] == "***REDACTED***"
    assert dbg["response"]["headers"]["set-cookie"] == "***REDACTED***"
    assert "topsecret" not in json.dumps(dbg)
    assert "super-secret-bearer" not in json.dumps(dbg)


@pytest.mark.asyncio
@respx.mock
async def test_no_redact_keeps_auth_header(minimal_specs_dir: Path) -> None:
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(500, json={})
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=True, redact=False))
    await d.connect()
    try:
        result = await d.call(action, {})
    finally:
        await d.close()
    assert result["debug"]["request"]["headers"]["Authorization"] == "Bearer super-secret-bearer"


@pytest.mark.asyncio
@respx.mock
async def test_credential_shaped_body_values_redacted(minimal_specs_dir: Path) -> None:
    """A token echoed in a response body must be scrubbed even though TE issues
    bearer tokens out-of-band (review heads-up: header-only redaction leaks)."""
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(
            500, json={"apiToken": "leaked-value", "nested": {"password": "hunter2"}, "ok": 1}
        )
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=True))
    await d.connect()
    try:
        result = await d.call(action, {})
    finally:
        await d.close()
    body = result["debug"]["response"]["body"]
    assert body["apiToken"] == "***REDACTED***"
    assert body["nested"]["password"] == "***REDACTED***"
    assert body["ok"] == 1
    assert "leaked-value" not in json.dumps(result["debug"])
    assert "hunter2" not in json.dumps(result["debug"])


@pytest.mark.asyncio
@respx.mock
async def test_capture_errors_does_not_attach_to_success(minimal_specs_dir: Path) -> None:
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(200, json={"tests": []})
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=True, capture="errors"))
    await d.connect()
    try:
        result = await d.call(action, {})
    finally:
        await d.close()
    assert result == {"tests": []}  # untouched — no debug attached on success


@pytest.mark.asyncio
@respx.mock
async def test_capture_all_attaches_to_dict_success(minimal_specs_dir: Path) -> None:
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(200, json={"tests": []})
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=True, capture="all"))
    await d.connect()
    try:
        result = await d.call(action, {})
    finally:
        await d.close()
    assert isinstance(result, dict)
    assert result["tests"] == []
    assert result["debug"]["response"]["status_code"] == 200


@pytest.mark.asyncio
@respx.mock
async def test_transport_error_path_captured(minimal_specs_dir: Path) -> None:
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=True))
    await d.connect()
    try:
        result = await d.call(action, {})
    finally:
        await d.close()
    assert result["error"] is True
    assert "Request failed" in result["message"]
    assert result["debug"]["response"]["transport_error"]
    assert result["debug"]["response"]["status_code"] is None


# ---------------------------------------------------------------------------
# redaction helpers (unit) — credential-shaped keys, values, headers, URLs
# ---------------------------------------------------------------------------


def test_redact_url_masks_credential_query_params() -> None:
    url = "https://api.thousandeyes.com/v7/tests?token=SECRETCURSOR&aid=99&cursor=abc"
    out = _redact_url(url, True, ())
    assert "SECRETCURSOR" not in out
    assert "aid=99" in out  # non-sensitive param preserved


def test_redact_url_masks_known_secret_literal() -> None:
    url = "https://api.thousandeyes.com/v7/tests?next=page2"
    out = _redact_url(url + "&sig=topsecretval", True, ("topsecretval",))
    assert "topsecretval" not in out


@pytest.mark.parametrize(
    "key", ["accessKey", "privateKey", "pwd", "signature", "client_secret", "X-Auth"]
)
def test_redact_value_broadened_keys(key: str) -> None:
    out = _redact_value({key: "leak", "ok": 1}, True)
    assert out[key] == "***REDACTED***"
    assert out["ok"] == 1


def test_redact_value_scrubs_secret_value_under_innocuous_key() -> None:
    """The SD-WAN heads-up: a token echoed as a VALUE under a harmless key."""
    out = _redact_value({"message": "rejected Bearer eyJabc123def456ghi token"}, True)
    assert "eyJabc123def456ghi" not in json.dumps(out)


def test_redact_value_scrubs_known_secret_literal_anywhere() -> None:
    out = _redact_value(
        {"echo": "the value is s3cr3t-token-literal here"}, True, ("s3cr3t-token-literal",)
    )
    assert "s3cr3t-token-literal" not in json.dumps(out)


def test_redact_headers_masks_credential_shaped_names() -> None:
    out = _redact_headers({"X-Api-Key": "k", "X-Auth-Token": "t", "Accept": "json"}, True)
    assert out["X-Api-Key"] == "***REDACTED***"
    assert out["X-Auth-Token"] == "***REDACTED***"
    assert out["Accept"] == "json"


# ---------------------------------------------------------------------------
# dispatcher integration: value leaks, success shapes, stderr, pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_bearer_token_echoed_in_body_is_scrubbed(minimal_specs_dir: Path) -> None:
    """The literal bearer token echoed back under an innocuous key must not
    survive into the capture even though its key isn't credential-shaped."""
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(500, json={"detail": "token super-secret-bearer is invalid"})
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=True))
    await d.connect()
    try:
        result = await d.call(action, {})
    finally:
        await d.close()
    assert "super-secret-bearer" not in json.dumps(result["debug"])


@pytest.mark.asyncio
@respx.mock
async def test_cursor_follow_url_token_redacted(
    minimal_specs_dir: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """A token embedded in the server-provided _links.next.href must be redacted
    in the capture (the URL bypasses structured query-param redaction). Also
    asserts the paginator does not leave a misleading per-page debug key in the
    stitched envelope, and that tool_name is threaded through the paginator."""
    respx.get("https://api.thousandeyes.com/v7/agents").mock(
        return_value=httpx.Response(
            200,
            json={
                "agents": [{"id": 1}],
                "_links": {
                    "next": {"href": "https://api.thousandeyes.com/v7/agents?token=NEXTPAGESECRET"}
                },
            },
        )
    )
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    action = next(name for name in index.by_action_name if name.startswith("get_agents"))
    d = Dispatcher(
        base_url="https://api.thousandeyes.com/v7",
        auth=ThousandEyesAuth(bearer_token="super-secret-bearer"),
        retry=RetryConfig(max_attempts=1),
        pagination=PaginationConfig(enabled=True, max_pages=2),
        debug=DebugConfig(enabled=True, capture="all"),
    )
    d.set_index(index)
    await d.connect()
    try:
        result = await d.call(action, {}, tool_name="agents")
    finally:
        await d.close()
    # The stitched envelope must not carry a misleading single-page debug key.
    assert "debug" not in result
    err = capfd.readouterr().err
    assert "[dispatcher][debug]" in err  # per-page captures still emitted
    assert '"tool": "agents"' in err  # tool_name threaded through the paginator
    assert "NEXTPAGESECRET" not in err  # cursor-href token redacted


@pytest.mark.asyncio
@respx.mock
async def test_list_success_capture_all_not_corrupted(minimal_specs_dir: Path) -> None:
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(200, json=[1, 2, 3])
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=True, capture="all"))
    await d.connect()
    try:
        result = await d.call(action, {})
    finally:
        await d.close()
    assert result == [1, 2, 3]  # array shape preserved, no debug wrapper


@pytest.mark.asyncio
@respx.mock
async def test_capture_all_does_not_clobber_upstream_debug_key(minimal_specs_dir: Path) -> None:
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(200, json={"tests": [], "debug": "from-upstream"})
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=True, capture="all"))
    await d.connect()
    try:
        result = await d.call(action, {})
    finally:
        await d.close()
    assert result["debug"] == "from-upstream"  # not overwritten


@pytest.mark.asyncio
@respx.mock
async def test_debug_line_emitted_to_stderr_redacted(
    minimal_specs_dir: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(403, json={"detail": "token super-secret-bearer bad"})
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=True))
    await d.connect()
    try:
        await d.call(action, {})
    finally:
        await d.close()
    err = capfd.readouterr().err
    assert "[dispatcher][debug]" in err
    assert "super-secret-bearer" not in err


@pytest.mark.asyncio
@respx.mock
async def test_error_body_returned_raw_to_caller(minimal_specs_dir: Path) -> None:
    """Documented scope: redaction covers the debug capture, not the primary
    result body (the data the caller requested)."""
    respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(400, json={"errorCode": "BAD", "field": "x"})
    )
    d, action = _dispatcher(minimal_specs_dir, DebugConfig(enabled=True))
    await d.connect()
    try:
        result = await d.call(action, {})
    finally:
        await d.close()
    assert result["body"] == {"errorCode": "BAD", "field": "x"}  # untouched
    assert result["debug"]["response"]["error_code"] == "BAD"
