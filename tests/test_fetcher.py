"""Tests for the fetcher (download + discover)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from thousand_eyes_mcp.fetcher import (
    KNOWN_SPEC_URLS,
    SpecContentInvalidError,
    SpecVersionUnknownError,
    fetch_spec,
    list_known_versions,
)
from thousand_eyes_mcp.fetcher.discover import (
    DiscoveryError,
    parse_discovery_html,
)


@pytest.mark.asyncio
async def test_fetch_unknown_version_raises(tmp_path: Path) -> None:
    with pytest.raises(SpecVersionUnknownError, match="No known download URL"):
        await fetch_spec("999.999.999", tmp_path)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_invalid_body_raises(tmp_path: Path) -> None:
    url = KNOWN_SPEC_URLS["7.0.88"]
    respx.get(url).mock(return_value=httpx.Response(200, text="{}"))
    with pytest.raises(SpecContentInvalidError):
        await fetch_spec("7.0.88", tmp_path)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_writes_file(tmp_path: Path) -> None:
    url = KNOWN_SPEC_URLS["7.0.88"]
    spec = "openapi: 3.0.1\npaths: {}\n"
    respx.get(url).mock(return_value=httpx.Response(200, text=spec))
    result = await fetch_spec("7.0.88", tmp_path)
    assert result.exists()
    assert result.read_text() == spec


def test_list_known_versions_empty_specs_dir(tmp_path: Path) -> None:
    rows = list_known_versions(tmp_path / "missing")
    assert all(r.cached is False for r in rows)
    assert {r.version for r in rows} == set(KNOWN_SPEC_URLS)


def test_list_known_versions_cached(tmp_path: Path) -> None:
    (tmp_path / "7.0.88").mkdir()
    (tmp_path / "7.0.88" / "api.yaml").write_text("openapi: 3.0.1\npaths: {}\n")
    rows = list_known_versions(tmp_path)
    cached = {r.version: r.cached for r in rows}
    assert cached.get("7.0.88") is True


def test_discover_empty_html_raises() -> None:
    with pytest.raises(DiscoveryError):
        parse_discovery_html("<html><body>nothing here</body></html>")


def test_discover_parses_pubhub_url() -> None:
    html = (
        '<a href="https://pubhub.devnetcloud.com/media/000-v7-apis/docs/'
        'reference/unified-oas/api.yaml">spec</a>'
    )
    out = parse_discovery_html(html)
    assert out == {
        "v7": "https://pubhub.devnetcloud.com/media/000-v7-apis/docs/reference/unified-oas/api.yaml"
    }
