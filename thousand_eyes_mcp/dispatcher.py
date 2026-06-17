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
import json as _json
import random
import re
import sys
import time
import urllib.parse
from typing import Any, TypeAlias

import httpx

from .auth import ThousandEyesAuth
from .config import DebugConfig, PaginationConfig, RetryConfig
from .loader import OperationSpec, SpecIndex
from .pagination import CursorPaginator, OffsetPaginator, Paginator

_MUTATING_METHODS = frozenset({"post", "put", "delete", "patch"})

_RESERVED_PARAM_KEYS = ("_max_pages", "_page_size", "_auto_follow", "_next_href")

# --- debug capture / redaction --------------------------------------------
_REDACTED = "***REDACTED***"
# Headers whose values are credentials. ThousandEyes uses ``Authorization:
# Bearer <token>``; cookies can carry session material in either direction.
_SENSITIVE_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "set-cookie"})
# Keys (header / body / query) whose values are credential-shaped. Matched as a
# case-insensitive substring so e.g. ``apiKey``/``api_key``/``X-Api-Key`` hit.
_SENSITIVE_KEY_RE = re.compile(
    r"token|secret|passw(or)?d|\bpwd\b|cookie|credential|api[-_]?key|access[-_]?key|"
    r"private[-_]?key|client[-_]?secret|session[-_]?id|signature|signing|"
    r"authorization|bearer|oauth|refresh|x-?auth",
    re.IGNORECASE,
)
# Credential-shaped *values* (matched regardless of key) — defends against the
# "token echoed in a response body under an innocuous key" leak class.
_VALUE_TOKEN_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}|eyJ[A-Za-z0-9._-]{8,}")
# Credential-shaped query params embedded inside a free-text string (e.g. a URL
# carried in a response body such as ``_links.next.href``). The key part is kept
# so the shape stays legible; the value is masked.
_SENSITIVE_QS_RE = re.compile(
    r"(?i)((?:token|secret|passw(?:or)?d|api[-_]?key|access[-_]?key|private[-_]?key|"
    r"client[-_]?secret|session[-_]?id|credential|signature|bearer|oauth|refresh)=)"
    r"[^&\s\"']+"
)
_MAX_DEBUG_BODY_CHARS = 8192


def _redact_text(text: str, secrets: tuple[str, ...]) -> str:
    """Mask known secret literals and credential-shaped substrings in a string."""
    out = text
    for secret in secrets:
        if secret and secret in out:
            out = out.replace(secret, _REDACTED)
    out = _VALUE_TOKEN_RE.sub(_REDACTED, out)
    return _SENSITIVE_QS_RE.sub(lambda m: m.group(1) + _REDACTED, out)


def _redact_headers(
    headers: dict[str, Any], redact: bool, secrets: tuple[str, ...] = ()
) -> dict[str, Any]:
    if not redact:
        return dict(headers)
    out: dict[str, Any] = {}
    for k, v in headers.items():
        if k.lower() in _SENSITIVE_HEADERS or _SENSITIVE_KEY_RE.search(k):
            out[k] = _REDACTED
        else:
            out[k] = _redact_text(v, secrets) if isinstance(v, str) else v
    return out


def _redact_value(obj: Any, redact: bool, secrets: tuple[str, ...] = ()) -> Any:
    """Recursively mask values by credential-shaped key AND by credential-shaped
    value content (so a secret under an innocuous key is still scrubbed)."""
    if not redact:
        return obj
    if isinstance(obj, dict):
        return {
            k: (
                _REDACTED
                if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k)
                else _redact_value(v, redact, secrets)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_value(i, redact, secrets) for i in obj]
    if isinstance(obj, str):
        return _redact_text(obj, secrets)
    return obj


def _redact_url(url: str, redact: bool, secrets: tuple[str, ...] = ()) -> str:
    """Redact credential-shaped query params (and secret literals) inside a URL.

    The cursor-follow path uses a server-provided absolute ``_links.next.href``
    whose query string is embedded in the URL itself — without this it would
    bypass the structured query-param redaction entirely."""
    if not redact:
        return url
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.query:
            pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            redacted = [
                (k, _REDACTED if _SENSITIVE_KEY_RE.search(k) else _redact_text(v, secrets))
                for k, v in pairs
            ]
            parts = parts._replace(query=urllib.parse.urlencode(redacted))
        url = urllib.parse.urlunsplit(parts)
    except Exception:  # pragma: no cover - defensive: never let redaction raise
        pass
    return _redact_text(url, secrets)


def _truncate_body(body: Any) -> Any:
    """Cap an oversized debug body so a capture stays pasteable."""
    if body is None or isinstance(body, (int, float, bool)):
        return body
    try:
        serialized = _json.dumps(body, default=str)
    except Exception:
        serialized = str(body)
    if len(serialized) <= _MAX_DEBUG_BODY_CHARS:
        return body
    return {
        "_truncated": True,
        "_original_chars": len(serialized),
        "preview": serialized[:_MAX_DEBUG_BODY_CHARS],
    }


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
        debug: DebugConfig | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._index: SpecIndex | None = None
        self._pagination_cfg = pagination or PaginationConfig()
        self._retry_cfg = retry or RetryConfig()
        self._default_aid = default_account_group_id
        self._debug = debug or DebugConfig()
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

    async def call(
        self, action_name: str, params: dict[str, Any], tool_name: str | None = None
    ) -> DispatchResult:
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

        return await self._execute_with_retry(op, params, tool_name)

    async def _execute_with_retry(
        self, op: OperationSpec, params: dict[str, Any], tool_name: str | None = None
    ) -> DispatchResult:
        clean_params, overrides = _strip_reserved(params)
        auto_follow = overrides.get("_auto_follow", True)

        paginator = (
            _pick_paginator(op.pagination)
            if (self._pagination_cfg.enabled and auto_follow)
            else None
        )

        if paginator is None:
            return await self._execute(op, params, tool_name)

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

        # Thread tool_name through the paginator (whose executor signature is
        # ``(op, params)``) via a closure so per-page debug captures are tagged.
        # in_band_attach=False: per-page success captures go to stderr only, not
        # into the page dict, so the stitched envelope is not corrupted.
        async def _executor(page_op: OperationSpec, page_params: dict[str, Any]) -> Any:
            return await self._execute(page_op, page_params, tool_name, in_band_attach=False)

        return await paginator.paginate(
            op,
            clean_params,
            _executor,
            max_pages=max_pages,
            page_size=page_size,
        )

    async def _execute(
        self,
        op: OperationSpec,
        raw_params: dict[str, Any],
        tool_name: str | None = None,
        in_band_attach: bool = True,
    ) -> DispatchResult:
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

        debug_on = self._debug.enabled
        method = op.method.upper()
        started = time.monotonic()

        try:
            response = await self._send_with_retry(
                method=method,
                url=url,
                params=send_params,
                json=body_json,
                headers=headers,
                retryable=self._is_retryable(op.method),
            )
        except httpx.RequestError as exc:
            result: dict[str, Any] = {"error": True, "message": f"Request failed: {exc}"}
            if debug_on:
                dbg = self._build_debug(
                    tool_name,
                    op,
                    method,
                    url,
                    send_params,
                    body_json,
                    headers,
                    status=None,
                    resp_headers=None,
                    resp_body=None,
                    transport_error=str(exc),
                    started=started,
                )
                self._emit_debug(dbg)
                result["debug"] = dbg
            return result

        if response.is_error:
            body = _safe_json(response)
            result = {
                "error": True,
                "status_code": response.status_code,
                "message": f"HTTP {response.status_code}",
                "body": body,
            }
            if debug_on:
                dbg = self._build_debug(
                    tool_name,
                    op,
                    method,
                    url,
                    send_params,
                    body_json,
                    headers,
                    status=response.status_code,
                    resp_headers=dict(response.headers),
                    resp_body=body,
                    transport_error=None,
                    started=started,
                )
                self._emit_debug(dbg)
                result["debug"] = dbg
            return result

        success = _safe_json(response)
        if debug_on and self._debug.capture == "all":
            dbg = self._build_debug(
                tool_name,
                op,
                method,
                url,
                send_params,
                body_json,
                headers,
                status=response.status_code,
                resp_headers=dict(response.headers),
                resp_body=success,
                transport_error=None,
                started=started,
            )
            self._emit_debug(dbg)
            # Only dict-shaped successes can carry the attachment, and only when
            # not running under the paginator (in_band_attach) — otherwise the
            # paginator stitches pages keeping only page 0's scalars, leaving a
            # misleading single-page `debug` in the envelope. List/str successes
            # and a pre-existing upstream `debug` key are left untouched; those
            # captures are observed on stderr only. See issue #8 review.
            if in_band_attach and isinstance(success, dict) and "debug" not in success:
                success = {**success, "debug": dbg}
        return success

    def _build_debug(
        self,
        tool_name: str | None,
        op: OperationSpec,
        method: str,
        url: str,
        query: dict[str, Any] | None,
        body: Any,
        req_headers: dict[str, str],
        *,
        status: int | None,
        resp_headers: dict[str, Any] | None,
        resp_body: Any,
        transport_error: str | None,
        started: float,
    ) -> dict[str, Any]:
        """Assemble the structured debug capture, applying redaction."""
        redact = self._debug.redact
        # The upstream bearer token is the highest-value secret and can be echoed
        # back in error bodies/headers under arbitrary keys — scrub its literal
        # value everywhere, not just key-matched fields.
        secrets: tuple[str, ...] = ()
        if redact:
            token = getattr(self._auth, "_token", "") or ""
            if len(token) >= 6:
                secrets = (token,)
        request = {
            "method": method,
            "path": op.path,
            "url": _redact_url(url, redact, secrets),
            "query_params": _redact_value(query or {}, redact, secrets),
            "body": _truncate_body(_redact_value(body, redact, secrets)),
            "headers": _redact_headers(req_headers, redact, secrets),
        }
        response: dict[str, Any] | None = None
        if status is not None or resp_body is not None or transport_error is not None:
            error_code = resp_body.get("errorCode") if isinstance(resp_body, dict) else None
            response = {
                "status_code": status,
                "error_code": error_code,
                "headers": _redact_headers(resp_headers or {}, redact, secrets),
                "body": _truncate_body(_redact_value(resp_body, redact, secrets)),
            }
            if transport_error is not None:
                response["transport_error"] = _redact_text(transport_error, secrets)
        return {
            "tool": tool_name,
            "action": op.action_name,
            "operation_id": op.operation_id,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "request": request,
            "response": response,
        }

    @staticmethod
    def _emit_debug(debug_obj: dict[str, Any]) -> None:
        try:
            line = _json.dumps(debug_obj, default=str)
        except Exception:  # pragma: no cover - defensive
            line = str(debug_obj)
        print(f"[dispatcher][debug] {line}", file=sys.stderr)

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
