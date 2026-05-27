"""Tests for retry behavior in the Dispatcher."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from thousand_eyes_mcp.auth import ThousandEyesAuth
from thousand_eyes_mcp.config import PaginationConfig, RetryConfig
from thousand_eyes_mcp.dispatcher import Dispatcher
from thousand_eyes_mcp.loader import SpecLoader


def _make_dispatcher(retry_cfg: RetryConfig) -> Dispatcher:
    return Dispatcher(
        base_url="https://api.thousandeyes.com/v7",
        auth=ThousandEyesAuth(bearer_token="tok"),
        retry=retry_cfg,
        pagination=PaginationConfig(enabled=False),
    )


@pytest.mark.asyncio
@respx.mock
async def test_retries_on_503_then_succeeds(minimal_specs_dir: Path) -> None:
    route = respx.get("https://api.thousandeyes.com/v7/tests").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"tests": []}),
        ]
    )
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    d = _make_dispatcher(RetryConfig(max_attempts=3, backoff_base=0.0))
    d.set_index(index)
    await d.connect()
    try:
        action = next(name for name in index.by_action_name if name.startswith("get_tests"))
        result = await d.call(action, {})
    finally:
        await d.close()
    assert route.call_count == 2
    assert result == {"tests": []}


@pytest.mark.asyncio
@respx.mock
async def test_no_retry_on_post_by_default(minimal_specs_dir: Path) -> None:
    route = respx.post("https://api.thousandeyes.com/v7/alerts").mock(
        return_value=httpx.Response(503)
    )
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=True).load()
    d = _make_dispatcher(RetryConfig(max_attempts=3, backoff_base=0.0, retry_mutating=False))
    d.set_index(index)
    await d.connect()
    try:
        post_action = next(
            (op.action_name for op in index.by_action_name.values() if op.method == "post"),
            None,
        )
        assert post_action is not None
        result = await d.call(post_action, {"name": "x"})
    finally:
        await d.close()
    assert route.call_count == 1
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result.get("status_code") == 503


@pytest.mark.asyncio
@respx.mock
async def test_retry_exhausted_returns_error(minimal_specs_dir: Path) -> None:
    route = respx.get("https://api.thousandeyes.com/v7/tests").mock(
        return_value=httpx.Response(503)
    )
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    d = _make_dispatcher(RetryConfig(max_attempts=2, backoff_base=0.0))
    d.set_index(index)
    await d.connect()
    try:
        action = next(name for name in index.by_action_name if name.startswith("get_tests"))
        result = await d.call(action, {})
    finally:
        await d.close()
    assert route.call_count == 2
    assert isinstance(result, dict)
    assert result.get("status_code") == 503
