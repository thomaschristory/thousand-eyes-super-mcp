"""Dynamically register one FastMCP tool per ToolGroup.

Tool shape:
  name:        group slug (e.g. "tests", "agents")
  description: lists all actions with their params, generated from the spec
  args:
    action:    str — one of the derived action_names in this group
    params:    dict — keys/values vary by action, documented in description

NOTE: handler bodies close over per-group state (`group`, `dispatcher`,
`valid_actions`) via a factory function (`_register_group_tool`). A naive
loop-and-`def` would late-bind every handler to the last group — the factory
forces value capture at definition time.

The handler signature is kept minimal (`action`, `params`) so FastMCP's
pydantic schema generator only sees JSON-serialisable types.

All log lines route to stderr — stdout is reserved for the MCP JSON-RPC
stream on the default stdio transport.
"""

from __future__ import annotations

import sys
from typing import Any

from fastmcp import FastMCP

from .dispatcher import Dispatcher, DispatchResult
from .loader import OperationSpec, ParameterSpec, SpecIndex, ToolGroup


def _format_param(p: ParameterSpec) -> str:
    req = "" if p.required else "?"
    desc = f" — {p.description}" if p.description else ""
    default = f" (default: {p.default})" if p.default is not None else ""
    return f"{p.name}{req}: {p.type}{desc}{default}"


_PAGINATION_HINT = (
    "Pagination: paginated actions auto-stitch up to N pages and return "
    "{<list_key>, _links, _paginated: {...}}. Override per call with reserved "
    "params: _max_pages (int), _page_size (int), _auto_follow (bool — set False "
    "to force single-page mode)."
)

_BODY_HINT = (
    "Request bodies: for object bodies, pass the body fields at the TOP LEVEL of "
    "params — do NOT nest them under a 'body' key. Body-bearing actions list "
    "their fields below as 'body fields (top-level): ...'. Array/scalar bodies "
    "are the exception and are passed under a single 'body' key, as their "
    "per-action line shows."
)


def _format_body(op: OperationSpec) -> str:
    """Render the request-body signature for an action.

    When the body schema is introspectable, list the real top-level fields so a
    caller passes them directly in ``params``. An array-rooted body cannot be
    expressed as top-level params, so it is passed under a lone ``body`` key.
    Otherwise fall back to the opaque ``body: object`` form.
    """
    if op.body_fields:
        rendered = ", ".join(_format_param(p) for p in op.body_fields)
        return f"body fields (top-level): {rendered}"
    if op.body_array:
        return f"body: array — pass a JSON list under a lone 'body' key — {op.body_description}"
    return f"body: object (pass its fields at the top level) — {op.body_description}"


def _build_description(group: ToolGroup) -> str:
    lines = [group.display_tag, "", _PAGINATION_HINT]
    if any(op.has_body for op in group.operations):
        lines += ["", _BODY_HINT]
    lines += ["", "Actions:"]

    for op in group.operations:
        path_params = [p for p in op.parameters if p.location == "path"]
        query_params = [p for p in op.parameters if p.location == "query"]

        param_parts: list[str] = []
        for p in path_params:
            param_parts.append(_format_param(p))
        for p in query_params:
            param_parts.append(_format_param(p))
        if op.has_body:
            param_parts.append(_format_body(op))

        params_str = ", ".join(param_parts) if param_parts else ""
        summary = op.summary.strip() if op.summary else ""

        lines.append(f"  - {op.action_name}({params_str}) [{op.method.upper()}]")
        if summary:
            lines.append(f"    {summary}")

    lines.append("")
    lines.append("Pass 'action' as one of the action names above.")
    lines.append("Pass 'params' as a dict matching the action's parameter list.")

    return "\n".join(lines)


def register_tools(mcp: FastMCP, index: SpecIndex, dispatcher: Dispatcher) -> int:
    """Register one MCP tool per ToolGroup. Returns the number registered."""
    for group in index.groups:
        _register_group_tool(mcp, group, dispatcher)

    count = len(index.groups)
    print(f"[tools] Registered {count} MCP tools", file=sys.stderr)
    return count


def _register_group_tool(
    mcp: FastMCP,
    group: ToolGroup,
    dispatcher: Dispatcher,
) -> None:
    tool_name = group.name
    description = _build_description(group)
    valid_actions = frozenset(op.action_name for op in group.operations)

    async def tool_handler(
        action: str,
        params: dict[str, Any] | None = None,
    ) -> DispatchResult:
        if action not in valid_actions:
            return {
                "error": True,
                "message": (
                    f"Unknown action '{action}' for tool '{tool_name}'. "
                    f"Valid actions: {sorted(valid_actions)}"
                ),
            }
        return await dispatcher.call(action, params or {})

    tool_handler.__name__ = tool_name
    tool_handler.__doc__ = description

    mcp.tool(name=tool_name, description=description)(tool_handler)
