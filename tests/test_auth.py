"""Tests for ThousandEyes bearer-token auth."""

from __future__ import annotations

import httpx
import pytest

from thousand_eyes_mcp.auth import AuthError, ThousandEyesAuth


@pytest.mark.asyncio
async def test_header_returns_bearer() -> None:
    auth = ThousandEyesAuth(bearer_token="abc123")
    assert auth.header() == {"Authorization": "Bearer abc123"}


@pytest.mark.asyncio
async def test_login_no_token_raises() -> None:
    auth = ThousandEyesAuth(bearer_token="")
    async with httpx.AsyncClient() as client:
        with pytest.raises(AuthError, match="bearer token is not set"):
            await auth.login(client)


@pytest.mark.asyncio
async def test_login_with_token_succeeds() -> None:
    auth = ThousandEyesAuth(bearer_token="t")
    async with httpx.AsyncClient() as client:
        await auth.login(client)
    assert auth.header()["Authorization"] == "Bearer t"


def test_needs_refresh_always_false() -> None:
    auth = ThousandEyesAuth(bearer_token="t")
    assert auth.needs_refresh() is False
    assert auth.expires_in() is None


def test_header_without_token_raises() -> None:
    auth = ThousandEyesAuth(bearer_token="")
    with pytest.raises(AuthError, match="login"):
        auth.header()
