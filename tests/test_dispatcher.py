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
