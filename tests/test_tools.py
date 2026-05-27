"""Tests for FastMCP tool registration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from thousand_eyes_mcp.loader import SpecLoader
from thousand_eyes_mcp.tools import _build_description, register_tools


def test_build_description_lists_actions(minimal_specs_dir: Path) -> None:
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    for group in index.groups:
        desc = _build_description(group)
        assert "Actions:" in desc
        assert "Pagination" in desc
        for op in group.operations:
            assert op.action_name in desc


def test_register_tools_returns_count(minimal_specs_dir: Path) -> None:
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    mcp = MagicMock()
    mcp.tool = MagicMock(return_value=lambda fn: fn)
    dispatcher = MagicMock()
    count = register_tools(mcp, index, dispatcher)
    assert count == len(index.groups) > 0
