"""Tests for cursor and offset paginators."""

from __future__ import annotations

from typing import Any

import pytest

from thousand_eyes_mcp.loader import OperationSpec, ParameterSpec
from thousand_eyes_mcp.pagination import CursorPaginator, OffsetPaginator


def _make_op(pagination: str = "cursor") -> OperationSpec:
    return OperationSpec(
        operation_id="getAlerts",
        action_name="get_alerts",
        summary="",
        method="get",
        path="/alerts",
        tag="Alerts",
        parameters=[
            ParameterSpec(name="cursor", location="query"),
            ParameterSpec(name="max", location="query", type="integer"),
        ],
        has_body=False,
        pagination=pagination,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_cursor_paginator_stops_when_no_next_link() -> None:
    op = _make_op("cursor")
    pages = [
        {"alerts": [1, 2], "_links": {"next": {"href": "https://api.te.com/v7/alerts?cursor=p2"}}},
        {"alerts": [3, 4], "_links": {"self": {"href": "x"}}},
    ]
    call_count = {"n": 0}

    async def executor(_op: OperationSpec, params: dict[str, Any]) -> dict[str, Any]:
        i = call_count["n"]
        call_count["n"] += 1
        return pages[i]

    result = await CursorPaginator().paginate(op, {}, executor, max_pages=5, page_size=None)
    assert result["alerts"] == [1, 2, 3, 4]
    assert result["_paginated"]["pages"] == 2
    assert result["_paginated"]["truncated"] is False
    assert result["_paginated"]["next_cursor"] is None


@pytest.mark.asyncio
async def test_cursor_paginator_truncates_at_max_pages() -> None:
    op = _make_op("cursor")

    async def executor(_op: OperationSpec, params: dict[str, Any]) -> dict[str, Any]:
        return {"alerts": [1], "_links": {"next": {"href": "https://x/next"}}}

    result = await CursorPaginator().paginate(op, {}, executor, max_pages=2, page_size=None)
    assert result["_paginated"]["pages"] == 2
    assert result["_paginated"]["truncated"] is True
    assert result["_paginated"]["next_cursor"] == {"next_href": "https://x/next"}


@pytest.mark.asyncio
async def test_offset_paginator_stops_on_short_page() -> None:
    op = OperationSpec(
        operation_id="getThings",
        action_name="get_things",
        summary="",
        method="get",
        path="/things",
        tag="Things",
        parameters=[
            ParameterSpec(name="offset", location="query", type="integer"),
            ParameterSpec(name="limit", location="query", type="integer"),
        ],
        has_body=False,
        pagination="offset",
    )
    sequence = [
        {"things": [1, 2, 3]},
        {"things": [4]},  # short — stop
    ]
    idx = {"n": 0}

    async def executor(_op: OperationSpec, _params: dict[str, Any]) -> dict[str, Any]:
        i = idx["n"]
        idx["n"] += 1
        return sequence[i]

    result = await OffsetPaginator().paginate(op, {}, executor, max_pages=5, page_size=3)
    assert result["things"] == [1, 2, 3, 4]
    assert result["_paginated"]["pages"] == 2
    assert result["_paginated"]["truncated"] is False
