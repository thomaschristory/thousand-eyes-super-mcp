"""Pagination strategies for ThousandEyes endpoints.

Two strategies, detected at spec-load time and stored on
``OperationSpec.pagination``:

- "cursor": opaque cursor token. Server returns ``_links.next.href`` in the
  response body (a fully-qualified URL with the next cursor baked in). The
  paginator follows that URL directly via ``executor_url``. Stops when
  ``_links.next`` is absent.
- "offset": classic offset + limit/max. Stops when a page returns fewer
  items than the configured size, or when the response list is empty.

When auto-follow fires, the paginator wraps the response:

    {
        "<list_key>": [...combined items...],
        "_links": {...last seen...},
        "_paginated": {
            "pages": N,
            "truncated": bool,
            "next_cursor": dict | None,
        },
    }

Single-page calls still return the wrapped shape so the LLM can rely on the
``_paginated`` key as the auto-follow signal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .loader import OperationSpec

Executor = Callable[[OperationSpec, dict[str, Any]], Awaitable[Any]]

# ThousandEyes wraps list collections under various names per endpoint family
# (tests/agents/alerts/events). Fall back to the first list-typed top-level
# key (excluding _links).
_KNOWN_LIST_KEYS = (
    "tests",
    "agents",
    "alerts",
    "events",
    "results",
    "items",
    "data",
)


class Paginator(Protocol):
    async def paginate(
        self,
        op: OperationSpec,
        params: dict[str, Any],
        executor: Executor,
        max_pages: int,
        page_size: int | None,
    ) -> dict[str, Any]: ...


def _first_list_key(page: dict[str, Any]) -> str | None:
    if not isinstance(page, dict):
        return None
    for known in _KNOWN_LIST_KEYS:
        if isinstance(page.get(known), list):
            return known
    for key, value in page.items():
        if key.startswith("_"):
            continue
        if isinstance(value, list):
            return str(key)
    return None


def _wrap(
    pages: list[dict[str, Any]],
    truncated: bool,
    next_cursor: dict[str, Any] | None,
    list_key: str | None = None,
) -> dict[str, Any]:
    """Merge ``pages`` into a single envelope under the discovered list key."""
    if not pages:
        return {
            "_paginated": {"pages": 0, "truncated": False, "next_cursor": None},
        }

    first = pages[0] if isinstance(pages[0], dict) else {}
    if list_key is None:
        list_key = _first_list_key(first)

    stitched: list[Any] = []
    if list_key is not None:
        for page in pages:
            if isinstance(page, dict):
                items = page.get(list_key)
                if isinstance(items, list):
                    stitched.extend(items)

    out: dict[str, Any] = {k: v for k, v in first.items() if k != list_key}
    if list_key is not None:
        out[list_key] = stitched
    out["_paginated"] = {
        "pages": len(pages),
        "truncated": truncated,
        "next_cursor": next_cursor,
    }
    return out


class OffsetPaginator:
    """offset + limit/max pagination. Stops on a short page or an empty page."""

    async def paginate(
        self,
        op: OperationSpec,
        params: dict[str, Any],
        executor: Executor,
        max_pages: int,
        page_size: int | None,
    ) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        current = dict(params)

        # ``max`` is the ThousandEyes idiom; some older endpoints use ``limit``.
        size_param = next(
            (k for k in ("limit", "max", "pageSize") if k in current),
            "limit",
        )
        effective_size: int | None = page_size
        if effective_size is None and current.get(size_param) is not None:
            try:
                effective_size = int(current[size_param])
            except (TypeError, ValueError):
                effective_size = None

        offset = int(current.get("offset", 0) or 0)
        next_cursor: dict[str, Any] | None = None
        truncated = False

        while len(pages) < max_pages:
            current["offset"] = offset
            if effective_size is not None:
                current[size_param] = effective_size
            page = await executor(op, current)
            page_dict = page if isinstance(page, dict) else {}
            pages.append(page_dict)

            list_key = _first_list_key(page_dict)
            items = page_dict.get(list_key) if list_key else None
            count = len(items) if isinstance(items, list) else 0

            if count == 0:
                break
            if effective_size is None:
                break
            if count < effective_size:
                break

            offset += count
        else:
            truncated = True
            next_cursor = {"offset": offset}
            if effective_size is not None:
                next_cursor[size_param] = effective_size

        return _wrap(pages, truncated=truncated, next_cursor=next_cursor)


class CursorPaginator:
    """Cursor pagination via ``_links.next.href``.

    Stops when ``_links.next`` is absent in the response. The href is a
    fully-qualified URL so we hand it back to the dispatcher as a
    ``_next_href`` override and let it execute against the original op.
    """

    async def paginate(
        self,
        op: OperationSpec,
        params: dict[str, Any],
        executor: Executor,
        max_pages: int,
        page_size: int | None,
    ) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        current = dict(params)
        if page_size is not None:
            # ThousandEyes accepts ``max`` (Alerts) or ``window`` (Test Results)
            # — pick the first param the op actually declares.
            for key in ("max", "limit", "pageSize"):
                if any(p.name == key and p.location == "query" for p in op.parameters):
                    current[key] = page_size
                    break

        next_href: str | None = None
        truncated = False

        while len(pages) < max_pages:
            call_params = dict(current)
            if next_href is not None:
                call_params["_next_href"] = next_href
            page = await executor(op, call_params)
            page_dict = page if isinstance(page, dict) else {}
            pages.append(page_dict)

            links = page_dict.get("_links") if isinstance(page_dict, dict) else None
            next_link = links.get("next") if isinstance(links, dict) else None
            href = next_link.get("href") if isinstance(next_link, dict) else None

            if not href:
                next_href = None
                break
            next_href = str(href)
        else:
            truncated = True

        next_cursor_obj = {"next_href": next_href} if (truncated and next_href) else None
        return _wrap(pages, truncated=truncated, next_cursor=next_cursor_obj)
