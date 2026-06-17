"""Startup ordering: the bearer token must be validated before spec loading (#3).

Loading (and possibly auto-fetching) the spec is expensive; there's no point
doing it when the ThousandEyes bearer token is missing and every API call is
guaranteed to fail. The check must fire immediately after config load, before
``SpecLoader``.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import pytest

from thousand_eyes_mcp.auth import require_credentials
from thousand_eyes_mcp.server import _connect_and_register


def _args(config: str) -> argparse.Namespace:
    return argparse.Namespace(
        config=config,
        version=None,
        transport="stdio",
        host=None,
        port=None,
        read_write=False,
        insecure_allow_public=False,
        max_actions_per_tool=None,
        debug=None,
        debug_all_calls=None,
        debug_no_redact=None,
    )


def test_require_credentials_raises_when_missing() -> None:
    with pytest.raises(RuntimeError, match="bearer token is not set"):
        require_credentials("")


def test_require_credentials_ok() -> None:
    require_credentials("a-token")  # must not raise


def test_credentials_validated_before_spec_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the token missing AND specs absent (auto_fetch off), the credentials
    error must win — proving the check runs before SpecLoader's
    FileNotFoundError."""
    monkeypatch.delenv("THOUSANDEYES_BEARER_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env reachable from here

    cfg = tmp_path / "thousand-eyes-mcp.yaml"
    cfg.write_text(
        "thousand_eyes_mcp:\n"
        f"  specs_dir: {tmp_path / 'no-such-specs'}\n"
        "  active_version: '7.0.88'\n"
        "  auto_fetch: false\n"
    )

    with pytest.raises(RuntimeError, match="bearer token is not set"):
        asyncio.run(_connect_and_register(_args(str(cfg))))
