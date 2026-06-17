"""Tests for the Dispatcher."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from thousand_eyes_mcp.auth import ThousandEyesAuth
from thousand_eyes_mcp.config import PaginationConfig, RetryConfig
from thousand_eyes_mcp.dispatcher import Dispatcher
from thousand_eyes_mcp.loader import SpecLoader


@pytest.mark.asyncio
@respx.mock
async def test_dispatcher_calls_upstream_with_bearer(minimal_specs_dir: Path) -> None:
    route = respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(200, json={"tests": []})
    )
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    auth = ThousandEyesAuth(bearer_token="tok")
    d = Dispatcher(
        base_url="https://api.thousandeyes.com/v7",
        auth=auth,
        retry=RetryConfig(max_attempts=1),
        pagination=PaginationConfig(enabled=False),
    )
    d.set_index(index)
    await d.connect()
    try:
        action = next(name for name in index.by_action_name if name.startswith("get_tests"))
        result = await d.call(action, {})
    finally:
        await d.close()

    assert route.called
    sent = route.calls[0].request
    assert sent.headers["Authorization"] == "Bearer tok"
    assert result == {"tests": []}


@pytest.mark.asyncio
@respx.mock
async def test_dispatcher_injects_default_aid(minimal_specs_dir: Path) -> None:
    route = respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(200, json={"tests": []})
    )
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    d = Dispatcher(
        base_url="https://api.thousandeyes.com/v7",
        auth=ThousandEyesAuth(bearer_token="tok"),
        retry=RetryConfig(max_attempts=1),
        pagination=PaginationConfig(enabled=False),
        default_account_group_id="42",
    )
    d.set_index(index)
    await d.connect()
    try:
        action = next(name for name in index.by_action_name if name.startswith("get_tests"))
        await d.call(action, {})
    finally:
        await d.close()

    assert route.called
    assert route.calls[0].request.url.params.get("aid") == "42"


@pytest.mark.asyncio
@respx.mock
async def test_dispatcher_substitutes_path_params(minimal_specs_dir: Path) -> None:
    route = respx.get("https://api.thousandeyes.com/v7/tests/abc").mock(
        return_value=httpx.Response(200, json={"id": "abc"})
    )
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    d = Dispatcher(
        base_url="https://api.thousandeyes.com/v7",
        auth=ThousandEyesAuth(bearer_token="tok"),
        retry=RetryConfig(max_attempts=1),
        pagination=PaginationConfig(enabled=False),
    )
    d.set_index(index)
    await d.connect()
    try:
        single = next(
            (op for op in index.by_action_name.values() if "{testId}" in op.path),
            None,
        )
        assert single is not None
        result = await d.call(single.action_name, {"testId": "abc"})
    finally:
        await d.close()
    assert route.called
    assert result == {"id": "abc"}


@pytest.mark.asyncio
@respx.mock
async def test_dispatcher_missing_path_param_returns_error(minimal_specs_dir: Path) -> None:
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    single = next(op for op in index.by_action_name.values() if "{testId}" in op.path)

    d = Dispatcher(
        base_url="https://api.thousandeyes.com/v7",
        auth=ThousandEyesAuth(bearer_token="tok"),
        retry=RetryConfig(max_attempts=1),
        pagination=PaginationConfig(enabled=False),
    )
    d.set_index(index)
    await d.connect()
    try:
        result = await d.call(single.action_name, {})
    finally:
        await d.close()
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert "Missing required path param" in result["message"]


def _rw_post_dispatcher(minimal_specs_dir: Path) -> tuple[Dispatcher, str]:
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=True).load()
    action = next(name for name in index.by_action_name if name.startswith("post_alerts"))
    d = Dispatcher(
        base_url="https://api.thousandeyes.com/v7",
        auth=ThousandEyesAuth(bearer_token="tok"),
        retry=RetryConfig(max_attempts=1),
        pagination=PaginationConfig(enabled=False),
    )
    d.set_index(index)
    return d, action


@pytest.mark.asyncio
@respx.mock
async def test_post_unwraps_lone_body_key(minimal_specs_dir: Path) -> None:
    """A caller following the old `body: object` schema sends {"body": {...}};
    the dispatcher must unwrap it so the API receives the real fields."""
    import json as _json

    route = respx.post("https://api.thousandeyes.com/v7/alerts").mock(
        return_value=httpx.Response(201, json={"id": "r1"})
    )
    d, action = _rw_post_dispatcher(minimal_specs_dir)
    await d.connect()
    try:
        await d.call(action, {"body": {"ruleName": "x", "severity": 2}})
    finally:
        await d.close()
    assert route.called
    assert _json.loads(route.calls[0].request.content) == {"ruleName": "x", "severity": 2}


@pytest.mark.asyncio
@respx.mock
async def test_post_top_level_fields_passthrough(minimal_specs_dir: Path) -> None:
    import json as _json

    route = respx.post("https://api.thousandeyes.com/v7/alerts").mock(
        return_value=httpx.Response(201, json={"id": "r1"})
    )
    d, action = _rw_post_dispatcher(minimal_specs_dir)
    await d.connect()
    try:
        await d.call(action, {"ruleName": "x", "severity": 2})
    finally:
        await d.close()
    assert _json.loads(route.calls[0].request.content) == {"ruleName": "x", "severity": 2}


@pytest.mark.asyncio
@respx.mock
async def test_post_genuine_body_field_alongside_others_not_unwrapped(
    minimal_specs_dir: Path,
) -> None:
    """A real field literally named `body` next to other fields must be left
    intact — only a *lone* `body` key is treated as the old wrapper."""
    import json as _json

    route = respx.post("https://api.thousandeyes.com/v7/alerts").mock(
        return_value=httpx.Response(201, json={"id": "r1"})
    )
    d, action = _rw_post_dispatcher(minimal_specs_dir)
    await d.connect()
    try:
        await d.call(action, {"body": "text", "name": "n"})
    finally:
        await d.close()
    assert _json.loads(route.calls[0].request.content) == {"body": "text", "name": "n"}


@pytest.mark.asyncio
@respx.mock
async def test_post_unwraps_lone_body_non_dict(minimal_specs_dir: Path) -> None:
    """The lone-`body` unwrap applies to any value type, e.g. a list body."""
    import json as _json

    route = respx.post("https://api.thousandeyes.com/v7/alerts").mock(
        return_value=httpx.Response(201, json={"ok": True})
    )
    d, action = _rw_post_dispatcher(minimal_specs_dir)
    await d.connect()
    try:
        await d.call(action, {"body": [1, 2, 3]})
    finally:
        await d.close()
    assert _json.loads(route.calls[0].request.content) == [1, 2, 3]


@pytest.mark.asyncio
@respx.mock
async def test_post_lone_declared_body_field_not_unwrapped() -> None:
    """When the operation's schema genuinely declares a single top-level field
    named `body`, a compliant caller sends {"body": <value>} and it must reach
    the API intact — the unwrap must NOT fire. See issue #9 review."""
    import json as _json

    from thousand_eyes_mcp.loader import OperationSpec, ParameterSpec, SpecIndex

    op = OperationSpec(
        operation_id="createNote",
        action_name="post_notes",
        summary="Create a note",
        method="post",
        path="/notes",
        tag="Notes",
        has_body=True,
        body_description="Note body",
        body_fields=[ParameterSpec(name="body", location="body", required=True, type="string")],
    )
    index = SpecIndex(by_action_name={"post_notes": op})

    route = respx.post("https://api.thousandeyes.com/v7/notes").mock(
        return_value=httpx.Response(201, json={"id": "n1"})
    )
    d = Dispatcher(
        base_url="https://api.thousandeyes.com/v7",
        auth=ThousandEyesAuth(bearer_token="tok"),
        retry=RetryConfig(max_attempts=1),
        pagination=PaginationConfig(enabled=False),
    )
    d.set_index(index)
    await d.connect()
    try:
        await d.call("post_notes", {"body": "hello"})
    finally:
        await d.close()
    assert _json.loads(route.calls[0].request.content) == {"body": "hello"}


@pytest.mark.asyncio
@respx.mock
async def test_dispatcher_unknown_action() -> None:
    from thousand_eyes_mcp.loader import SpecIndex

    d = Dispatcher(
        base_url="https://api.thousandeyes.com/v7",
        auth=ThousandEyesAuth(bearer_token="tok"),
        retry=RetryConfig(max_attempts=1),
        pagination=PaginationConfig(enabled=False),
    )
    d.set_index(SpecIndex())
    await d.connect()
    try:
        result = await d.call("does_not_exist", {})
    finally:
        await d.close()
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert "Unknown action" in result["message"]
