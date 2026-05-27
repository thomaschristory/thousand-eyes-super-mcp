"""Loads thousand-eyes-mcp.yaml and resolves ${ENV_VAR} interpolation."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

# Minimum bearer-token lengths. Below the hard floor we refuse to start;
# between the soft and hard floors we emit a stderr WARNING. 16 base64 chars
# ≈ 96 bits of entropy, enough to resist online brute force when paired with
# rate-limited logging.
_TOKEN_HARD_MIN = 8
_TOKEN_SOFT_MIN = 16

DEFAULT_CONFIG_PATH = "thousand-eyes-mcp.yaml"


def resolve_config_path(path: str, *, explicit: bool) -> tuple[str, bool]:
    """Resolve the effective config path.

    Returns ``(effective_path, used_legacy)``. ThousandEyes only ever shipped
    one config filename, so ``used_legacy`` is always False — the tuple shape
    is preserved for parity with sibling projects that did have a rename.
    """
    return path, False


@dataclass
class RetryConfig:
    """Retry policy for transient HTTP failures from the ThousandEyes API."""

    max_attempts: int = 3  # total attempts including the first try
    statuses: tuple[int, ...] = (429, 502, 503, 504)
    backoff_base: float = 0.5  # seconds; first backoff is base * 2**0
    backoff_cap: float = 8.0  # upper bound on a single backoff
    retry_mutating: bool = False  # by default, only GET is retried


@dataclass
class ThousandEyesConfig:
    """Upstream API connection settings.

    ThousandEyes is a SaaS — `base_url` is fixed to the production URL by
    default but can be overridden for federal/regional endpoints.
    """

    base_url: str = "https://api.thousandeyes.com/v7"
    bearer_token: str = ""
    account_group_id: str = ""  # optional default ``aid`` query param
    verify_ssl: bool = True
    timeout: float = 30.0
    retries: RetryConfig = field(default_factory=RetryConfig)


@dataclass
class PaginationConfig:
    enabled: bool = True
    max_pages: int = 5
    page_size: int | None = None


@dataclass
class ThousandEyesMcpConfig:
    specs_dir: str = "./specs"
    active_version: str = "7.0.88"
    max_actions_per_tool: int = 80  # 0 disables splitting (one tool per section)
    pagination: PaginationConfig = field(default_factory=PaginationConfig)
    auto_fetch: bool = True


_VALID_AUTH_TYPES: frozenset[str] = frozenset({"none", "bearer"})


@dataclass
class TransportAuthConfig:
    """Authentication for the HTTP transports (SSE, streamable-http).

    type='none' — no auth (only safe on loopback or behind a trusted proxy).
    type='bearer' — require `Authorization: Bearer <token>` on every request.
    """

    type: Literal["none", "bearer"] = "none"
    token: str = ""


@dataclass
class TransportConfig:
    mode: str = "stdio"  # stdio | sse | streamable-http
    host: str = "127.0.0.1"
    port: int = 8000
    auth: TransportAuthConfig = field(default_factory=TransportAuthConfig)


@dataclass
class AppConfig:
    thousand_eyes: ThousandEyesConfig = field(default_factory=ThousandEyesConfig)
    thousand_eyes_mcp: ThousandEyesMcpConfig = field(default_factory=ThousandEyesMcpConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)


_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def _interpolate(value: str) -> str:
    """Substitute ${VAR} from os.environ; missing → empty string + stderr WARNING (stdout would corrupt stdio MCP JSON-RPC stream)."""

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        result = os.environ.get(var_name, "")
        if not result:
            print(f"[config] WARNING: env var '{var_name}' is not set", file=sys.stderr)
        return result

    return _ENV_RE.sub(replacer, value)


def _interpolate_dict(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _interpolate_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_dict(i) for i in obj]
    if isinstance(obj, str):
        return _interpolate(obj)
    return obj


def load_config(path: str = DEFAULT_CONFIG_PATH) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = yaml.safe_load(config_path.read_text()) or {}
    raw = _interpolate_dict(raw)

    te_raw = raw.get("thousand_eyes", {}) or {}
    mcp_raw = raw.get("thousand_eyes_mcp", {}) or {}
    transport_raw = raw.get("transport", {}) or {}

    retries_raw = te_raw.get("retries", {}) or {}
    retry_defaults = RetryConfig()
    statuses_raw = retries_raw.get("statuses") or list(retry_defaults.statuses)
    retries = RetryConfig(
        max_attempts=int(retries_raw.get("max_attempts", retry_defaults.max_attempts)),
        statuses=tuple(int(s) for s in statuses_raw),
        backoff_base=float(retries_raw.get("backoff_base", retry_defaults.backoff_base)),
        backoff_cap=float(retries_raw.get("backoff_cap", retry_defaults.backoff_cap)),
        retry_mutating=bool(retries_raw.get("retry_mutating", retry_defaults.retry_mutating)),
    )

    thousand_eyes = ThousandEyesConfig(
        base_url=te_raw.get("base_url", "https://api.thousandeyes.com/v7"),
        bearer_token=te_raw.get("bearer_token", ""),
        account_group_id=str(te_raw.get("account_group_id", "") or ""),
        verify_ssl=bool(te_raw.get("verify_ssl", True)),
        timeout=float(te_raw.get("timeout", 30.0)),
        retries=retries,
    )

    pagination_raw = mcp_raw.get("pagination", {}) or {}
    pagination = PaginationConfig(
        enabled=bool(pagination_raw.get("enabled", True)),
        max_pages=int(pagination_raw.get("max_pages", 5)),
        page_size=(
            int(pagination_raw["page_size"])
            if pagination_raw.get("page_size") is not None
            else None
        ),
    )

    thousand_eyes_mcp = ThousandEyesMcpConfig(
        specs_dir=mcp_raw.get("specs_dir", "./specs"),
        active_version=str(mcp_raw.get("active_version", "7.0.88")),
        max_actions_per_tool=int(mcp_raw.get("max_actions_per_tool", 80)),
        pagination=pagination,
        auto_fetch=bool(mcp_raw.get("auto_fetch", True)),
    )

    auth_raw = transport_raw.get("auth", {}) or {}
    auth_type_str = str(auth_raw.get("type", "none"))
    auth_token = str(auth_raw.get("token", ""))

    if auth_type_str not in _VALID_AUTH_TYPES:
        raise ValueError(
            f"unknown transport.auth.type: {auth_type_str!r}. "
            f"Choose one of {sorted(_VALID_AUTH_TYPES)}."
        )
    auth_type: Literal["none", "bearer"] = cast(Literal["none", "bearer"], auth_type_str)

    if auth_type == "bearer" and not auth_token:
        raise ValueError(
            "transport.auth.type=bearer requires a non-empty transport.auth.token "
            "(set ${THOUSANDEYES_MCP_TOKEN} or equivalent, or check the env var is exported)."
        )
    if auth_type == "bearer" and len(auth_token) < _TOKEN_HARD_MIN:
        raise ValueError(
            f"transport.auth.token is too short ({len(auth_token)} chars); "
            f"require at least {_TOKEN_HARD_MIN} characters. "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    if auth_type == "bearer" and len(auth_token) < _TOKEN_SOFT_MIN:
        print(
            f"[config] WARNING: transport.auth.token is shorter than "
            f"{_TOKEN_SOFT_MIN} chars — recommend regenerating with "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"',
            file=sys.stderr,
        )
    if auth_type_str == "none" and auth_token:
        raise ValueError(
            "token configured but transport.auth.type=none — "
            "set type: bearer to enable it, or remove the token."
        )

    transport = TransportConfig(
        mode=transport_raw.get("mode", "stdio"),
        host=transport_raw.get("host", "127.0.0.1"),
        port=int(transport_raw.get("port", 8000)),
        auth=TransportAuthConfig(type=auth_type, token=auth_token),
    )

    return AppConfig(
        thousand_eyes=thousand_eyes,
        thousand_eyes_mcp=thousand_eyes_mcp,
        transport=transport,
    )
