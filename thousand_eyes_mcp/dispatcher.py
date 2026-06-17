"""httpx async client for ThousandEyes API calls.

Handles:
  - Bearer token authentication via ThousandEyesAuth
  - Path-param substitution; query/body routing per the OpenAPI spec
  - Optional default ``aid`` (account group ID) on every request
  - Configurable retry on transient failures (429/502/503/504 by default)
  - Pagination auto-follow with reserved-param overrides
  - Cursor pagination follows ``_links.next.href`` directly via ``_next_href``

Reserved param keys (stripped before HTTP):
  _max_pages    (int)   override config.pagination.max_pages
  _page_size    (int)   override config.pagination.page_size
  _auto_follow  (bool)  if False, force single-page mode for paginatable ops
  _next_href    (str)   internal — used by CursorPaginator to follow _links.next
"""

from __future__ import annotations

import asyncio
import random
import re
import sys
from typing import Any, TypeAlias

import httpx

from .auth import ThousandEyesAuth
from .config import PaginationConfig, RetryConfig
from .loader import OperationSpec, SpecIndex
from .pagination import CursorPaginator, OffsetPaginator, Paginator

_MUTATING_METHODS = frozenset({"post", "put", "delete", "patch"})

_RESERVED_PARAM_KEYS = ("_max_pages", "_page_size", "_auto_follow", "_next_href")


def _pick_paginator(style: str | None) -> Paginator | None:
    if style == "offset":
        return OffsetPaginator()
    if style == "cursor":
        return CursorPaginator()
    return None


DispatchResult: TypeAlias = dict[str, Any] | list[Any] | str


class Dispatcher:
    def __init__(
        self,
        base_url: str,
        auth: ThousandEyesAuth,
        verify_ssl: bool = True,
        timeout: float = 30.0,
        pagination: PaginationConfig | None = None,
        retry: RetryConfig | None = None,
        default_account_group_id: str = "",
    ):
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._index: SpecIndex | None = None
        self._pagination_cfg = pagination or PaginationConfig()
        self._retry_cfg = retry or RetryConfig()
        self._default_aid = default_account_group_id
        self._auth_lock = asyncio.Lock()

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            verify=verify_ssl,
            timeout=timeout,
            follow_redirects=False,
        )

    async def connect(self) -> None:
        """Verify the bearer token is set. No HTTP call is made."""
        await self._auth.login(self._client)

    async def close(self) -> None:
        await self._client.aclose()

    def set_index(self, index: SpecIndex) -> None:
        self._index = index

    async def call(self, action_name: str, params: dict[str, Any]) -> DispatchResult:
        if self._index is None:
            raise RuntimeError("SpecIndex not set — call set_index() first")

        op = self._index.by_action_name.get(action_name)
        if op is None:
            return {
                "error": True,
                "message": (
                    f"Unknown action: '{action_name}'. "
                    f"Check the tool description for valid action names."
                ),
            }

        return await self._execute_with_retry(op, params)

    async def _execute_with_retry(
        self, op: OperationSpec, params: dict[str, Any]
    ) -> DispatchResult:
        clean_params, overrides = _strip_reserved(params)
        auto_follow = overrides.get("_auto_follow", True)

        paginator = (
            _pick_paginator(op.pagination)
            if (self._pagination_cfg.enabled and auto_follow)
            else None
        )

        if paginator is None:
            return await self._execute_one_with_retry(op, params)

        max_pages_override = overrides.get("_max_pages")
        max_pages = (
            int(max_pages_override)
            if max_pages_override is not None
            else self._pagination_cfg.max_pages
        )
        page_size_override = overrides.get("_page_size")
        page_size = (
            int(page_size_override)
            if page_size_override is not None
            else self._pagination_cfg.page_size
        )

        return await paginator.paginate(
            op,
            clean_params,
            self._execute_one_with_retry,
            max_pages=max_pages,
            page_size=page_size,
        )

    async def _execute_one_with_retry(
        self, op: OperationSpec, params: dict[str, Any]
    ) -> DispatchResult:
        return await self._execute(op, params)

    async def _execute(self, op: OperationSpec, raw_params: dict[str, Any]) -> DispatchResult:
        params = dict(raw_params or {})
        next_href = params.pop("_next_href", None)

        path_param_names = {p.name for p in op.parameters if p.location == "path"}
        query_param_names = {p.name for p in op.parameters if p.location == "query"}

        path_params: dict[str, Any] = {}
        query_params: dict[str, Any] = {}
        body_params: dict[str, Any] = {}
        unknown_params: dict[str, Any] = {}

        for key, value in params.items():
            if value is None or key in _RESERVED_PARAM_KEYS:
                continue
            if key in path_param_names:
                path_params[key] = value
            elif key in query_param_names:
                query_params[key] = value
            elif op.has_body and op.method in ("post", "put", "patch"):
                body_params[key] = value
            else:
                unknown_params[key] = value

        if unknown_params:
            print(
                f"[dispatcher] WARNING: unrecognised params for '{op.action_name}': "
                f"{list(unknown_params.keys())} — forwarding as query params",
                file=sys.stderr,
            )
            query_params.update(unknown_params)

        # Inject default ``aid`` if the op accepts one and caller didn't set it.
        if self._default_aid and "aid" in query_param_names and "aid" not in query_params:
            query_params["aid"] = self._default_aid

        if next_href is not None:
            # Cursor follow — use the server-provided absolute URL verbatim.
            # The href already encodes the cursor and all original params, so
            # path_params/query_params are ignored for follow-up calls.
            url = str(next_href)
            send_params: dict[str, Any] | None = None
        else:
            url = op.path
            for name, value in path_params.items():
                url = url.replace(f"{{{name}}}", str(value))

            if "{" in url:
                missing = re.findall(r"\{([^}]+)\}", url)
                return {
                    "error": True,
                    "message": (
                        f"Missing required path param(s) for '{op.action_name}': {missing}. "
                        f"Provide them in the params dict."
                    ),
                }
            send_params = query_params or None

        # Defensive unwrap: the tool schema lists body fields at the top level,
        # but a caller that followed an older `body: object` convention may send
        # ``{"body": {...}}``. Since body params are forwarded verbatim, that
        # would double-wrap. If ``body`` is the *lone* body key AND the operation
        # does not declare a genuine field named ``body``, unwrap it. This keeps
        # old-convention callers working, lets array/scalar bodies be passed
        # under ``body``, and never corrupts an endpoint whose real schema has a
        # single top-level field literally named ``body``. See issue #9.
        body_json: Any = body_params or None
        if (
            len(body_params) == 1
            and "body" in body_params
            and not any(f.name == "body" for f in op.body_fields)
        ):
            body_json = body_params["body"]

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **self._auth.header(),
        }

        try:
            response = await self._send_with_retry(
                method=op.method.upper(),
                url=url,
                params=send_params,
                json=body_json,
                headers=headers,
                retryable=self._is_retryable(op.method),
            )
        except httpx.RequestError as exc:
            return {"error": True, "message": f"Request failed: {exc}"}

        if response.is_error:
            return {
                "error": True,
                "status_code": response.status_code,
                "message": f"HTTP {response.status_code}",
                "body": _safe_json(response),
            }

        return _safe_json(response)

    def _is_retryable(self, method: str) -> bool:
        if method.lower() in _MUTATING_METHODS:
            return self._retry_cfg.retry_mutating
        return True

    async def _send_with_retry(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json: Any,
        headers: dict[str, str],
        retryable: bool,
    ) -> httpx.Response:
        cfg = self._retry_cfg
        attempts = max(1, cfg.max_attempts) if retryable else 1
        last_response: httpx.Response | None = None

        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json,
                    headers=headers,
                )
            except httpx.RequestError:
                if attempt + 1 >= attempts:
                    raise
                await self._sleep_backoff(attempt)
                continue

            if response.status_code in cfg.statuses and attempt + 1 < attempts:
                last_response = response
                await self._sleep_backoff(attempt)
                continue

            return response

        assert last_response is not None
        return last_response

    async def _sleep_backoff(self, attempt: int) -> None:
        cfg = self._retry_cfg
        if cfg.backoff_base <= 0:
            return
        raw = min(cfg.backoff_cap, cfg.backoff_base * (2**attempt))
        half = raw / 2
        delay = half + random.uniform(0, half)
        await asyncio.sleep(delay)


def _safe_json(response: httpx.Response) -> DispatchResult:
    try:
        data = response.json()
    except Exception:
        return {"raw": response.text}
    if isinstance(data, (dict, list, str)):
        return data
    return {"raw": str(data)}


def _strip_reserved(
    params: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split reserved underscore keys out of params. Returns (clean, overrides)."""
    clean: dict[str, Any] = {}
    overrides: dict[str, Any] = {}
    for key, value in (params or {}).items():
        if key in _RESERVED_PARAM_KEYS:
            overrides[key] = value
        else:
            clean[key] = value
    return clean, overrides
