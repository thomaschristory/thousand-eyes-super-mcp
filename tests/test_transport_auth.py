"""Tests for transport_auth.decide_bind and BearerAuthMiddleware."""

from __future__ import annotations

import pytest

from thousand_eyes_mcp.transport_auth import BearerAuthMiddleware, decide_bind


def test_loopback_never_demoted() -> None:
    for host in ("127.0.0.1", "::1", "localhost", "[::1]"):
        effective, warnings = decide_bind(host, "none", insecure_ok=False)
        assert effective == host
        assert warnings == []


def test_public_demoted_with_none_auth() -> None:
    effective, warnings = decide_bind("0.0.0.0", "none", insecure_ok=False)
    assert effective == "127.0.0.1"
    assert any("Demoting" in w for w in warnings)


def test_public_kept_with_bearer_auth() -> None:
    effective, warnings = decide_bind("0.0.0.0", "bearer", insecure_ok=False)
    assert effective == "0.0.0.0"
    assert warnings == []


def test_public_kept_with_insecure_override() -> None:
    effective, warnings = decide_bind("0.0.0.0", "none", insecure_ok=True)
    assert effective == "0.0.0.0"
    assert warnings == []


def test_middleware_requires_token() -> None:
    with pytest.raises(ValueError, match="non-empty expected_token"):
        BearerAuthMiddleware(app=lambda *_: None, expected_token="")
