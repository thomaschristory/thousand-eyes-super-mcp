"""Tests for FastMCP tool registration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from thousand_eyes_mcp.loader import SpecLoader
from thousand_eyes_mcp.tools import _build_description, register_tools


class _StubDispatcher:
    """Stand-in for Dispatcher; registration never invokes it."""

    async def call(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"action": action, "params": params}


def test_build_description_lists_actions(minimal_specs_dir: Path) -> None:
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    for group in index.groups:
        desc = _build_description(group)
        assert "Actions:" in desc
        assert "Pagination" in desc
        for op in group.operations:
            assert op.action_name in desc


def test_register_tools_returns_count(minimal_specs_dir: Path) -> None:
    """Drive the real FastMCP registration path (not a mock), so a handler whose
    signature leaks an unserialisable type would fail here rather than slip
    through. See issue #6 / catalyst-sdwan #52."""
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    mcp = FastMCP("test")
    count = register_tools(mcp, index, _StubDispatcher())
    assert count == len(index.groups) > 0


async def test_registered_tool_schema_exposes_only_action_and_params(
    minimal_specs_dir: Path,
) -> None:
    """Regression for issue #6: fastmcp 3.x introspects the handler signature to
    build the tool's input schema. The handler must expose exactly `action` and
    `params` — leaking internal closures (e.g. a `Dispatcher` default arg) would
    raise PydanticSchemaGenerationError on the arbitrary type."""
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    mcp = FastMCP("test")
    register_tools(mcp, index, _StubDispatcher())
    tool_name = index.groups[0].name
    tool = await mcp.get_tool(tool_name)
    assert set(tool.parameters["properties"]) == {"action", "params"}
