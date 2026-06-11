"""config.py — application configuration.

Sources, highest priority first:

    1. constructor kwargs (programmatic / CLI overrides applied in server.py)
    2. environment variables (THOUSANDEYES_BEARER_TOKEN,
       THOUSANDEYES_ACCOUNT_GROUP_ID, THOUSANDEYES_VERIFY_SSL)
    3. the YAML config file (optional), with legacy ``${ENV}`` interpolation
    4. built-in defaults

The YAML file is **optional**: exporting the env vars (or putting them in a
``.env``) is enough to run, which is what makes the server work when installed
via ``uv tool install`` and launched by an MCP client (whose working directory
is not the user's project dir, so no YAML is on disk). See issue #3.
"""

from __future__ import annotations

import os
import re
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Minimum bearer-token lengths for the HTTP-transport auth token. Below the
# hard floor we refuse to start; between the soft and hard floors we emit a
# stderr WARNING. 16 base64 chars ≈ 96 bits of entropy, enough to resist online
# brute force when paired with rate-limited logging.
_TOKEN_HARD_MIN = 8
_TOKEN_SOFT_MIN = 16

DEFAULT_CONFIG_PATH = "thousand-eyes-mcp.yaml"

_VALID_AUTH_TYPES: frozenset[str] = frozenset({"none", "bearer"})

# Default ThousandEyes retry statuses (kept as a module constant so a YAML
# ``statuses: ~`` falls back here instead of crashing).
_DEFAULT_RETRY_STATUSES: tuple[int, ...] = (429, 502, 503, 504)


def resolve_config_path(path: str, *, explicit: bool) -> tuple[str, bool]:
    """Resolve the effective config path.

    Returns ``(effective_path, used_legacy)``. ThousandEyes only ever shipped
    one config filename, so ``used_legacy`` is always False — the tuple shape
    is preserved for parity with sibling projects that did have a rename.
    """
    del explicit  # unused; kept for signature parity with sibling projects
    return path, False


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class _Base(BaseModel):
    """Shared base: drop YAML ``null`` values so model defaults apply."""

    @model_validator(mode="before")
    @classmethod
    def _drop_nones(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data


class RetryConfig(_Base):
    """Retry policy for transient HTTP failures from the ThousandEyes API."""

    max_attempts: int = 3  # total attempts including the first try
    statuses: tuple[int, ...] = _DEFAULT_RETRY_STATUSES
    backoff_base: float = 0.5  # seconds; first backoff is base * 2**0
    backoff_cap: float = 8.0  # seconds; upper bound on a single backoff
    retry_mutating: bool = False  # by default, only GET is retried

    @model_validator(mode="before")
    @classmethod
    def _statuses_none_to_default(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("statuses") is None:
            data = {k: v for k, v in data.items() if k != "statuses"}
        return data


class ThousandEyesConfig(_Base):
    """Upstream API connection settings.

    ThousandEyes is a SaaS — ``base_url`` is fixed to the production URL by
    default but can be overridden for federal/regional endpoints.
    """

    base_url: str = "https://api.thousandeyes.com/v7"
    bearer_token: str = ""
    account_group_id: str = ""  # optional default ``aid`` query param
    verify_ssl: bool = True
    timeout: float = 30.0  # seconds, applied to every ThousandEyes HTTP request
    retries: RetryConfig = Field(default_factory=RetryConfig)


class PaginationConfig(_Base):
    enabled: bool = True
    max_pages: int = 5
    page_size: int | None = None


class ThousandEyesMcpConfig(_Base):
    specs_dir: str = "./specs"
    active_version: str = "7.0.88"
    max_actions_per_tool: int = 80  # 0 disables splitting (one tool per section)
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    auto_fetch: bool = True


class TransportAuthConfig(_Base):
    """Authentication for the HTTP transports (SSE, streamable-http).

    type='none' — no auth (only safe on loopback or behind a trusted proxy).
    type='bearer' — require `Authorization: Bearer <token>` on every request.
    """

    type: Literal["none", "bearer"] = "none"
    token: str = ""


class TransportConfig(_Base):
    mode: str = "stdio"  # stdio | sse | streamable-http
    host: str = "127.0.0.1"
    port: int = 8000
    auth: TransportAuthConfig = Field(default_factory=TransportAuthConfig)


# ---------------------------------------------------------------------------
# Settings sources
# ---------------------------------------------------------------------------

# YAML data for the current load_config() call. A ContextVar keeps load_config
# re-entrant and thread-safe without leaking the path into module state.
_yaml_data: ContextVar[dict[str, Any] | None] = ContextVar("_yaml_data", default=None)


class _YamlSource(PydanticBaseSettingsSource):
    """Feeds the (already interpolated) YAML dict into the settings model."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(_yaml_data.get() or {})


class _ThousandEyesEnvSource(PydanticBaseSettingsSource):
    """Maps the documented flat env vars onto ``thousand_eyes.*``.

    These take precedence over the YAML file so the token/verify flag can be
    overridden per-environment without editing the file (or with no file)."""

    _MAP: ClassVar[dict[str, str]] = {
        "THOUSANDEYES_BEARER_TOKEN": "bearer_token",
        "THOUSANDEYES_ACCOUNT_GROUP_ID": "account_group_id",
        "THOUSANDEYES_VERIFY_SSL": "verify_ssl",
    }

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        thousand_eyes: dict[str, Any] = {}
        for env_name, field in self._MAP.items():
            value = os.environ.get(env_name)
            if value:  # ignore unset and empty — let YAML/defaults stand
                thousand_eyes[field] = value  # pydantic coerces str -> bool
        return {"thousand_eyes": thousand_eyes} if thousand_eyes else {}


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    thousand_eyes: ThousandEyesConfig = Field(default_factory=ThousandEyesConfig)
    thousand_eyes_mcp: ThousandEyesMcpConfig = Field(default_factory=ThousandEyesMcpConfig)
    transport: TransportConfig = Field(default_factory=TransportConfig)

    @model_validator(mode="before")
    @classmethod
    def _drop_none_sections(cls, data: Any) -> Any:
        # A bare YAML section (e.g. `thousand_eyes:` with nothing under it)
        # parses to None; drop it so the section's defaults apply instead of
        # erroring.
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority: init kwargs > flat THOUSANDEYES_* env > YAML file > defaults.
        return (
            init_settings,
            _ThousandEyesEnvSource(settings_cls),
            _YamlSource(settings_cls),
        )


# ---------------------------------------------------------------------------
# Env var interpolation (legacy ${VAR} support inside YAML values)
# ---------------------------------------------------------------------------

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
    """Recursively interpolate env vars in all string values of a dict."""
    if isinstance(obj, dict):
        return {k: _interpolate_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_dict(i) for i in obj]
    if isinstance(obj, str):
        return _interpolate(obj)
    return obj


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _validate_transport_auth(transport: TransportConfig) -> None:
    auth_type = transport.auth.type
    token = transport.auth.token

    if auth_type not in _VALID_AUTH_TYPES:  # pragma: no cover - Literal guards this
        raise ValueError(
            f"unknown transport.auth.type: {auth_type!r}. "
            f"Choose one of {sorted(_VALID_AUTH_TYPES)}."
        )
    if auth_type == "bearer" and not token:
        raise ValueError(
            "transport.auth.type=bearer requires a non-empty transport.auth.token "
            "(set ${THOUSANDEYES_MCP_TOKEN} or equivalent, or check the env var is exported)."
        )
    if auth_type == "bearer" and len(token) < _TOKEN_HARD_MIN:
        raise ValueError(
            f"transport.auth.token is too short ({len(token)} chars); "
            f"require at least {_TOKEN_HARD_MIN} characters. "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    if auth_type == "bearer" and len(token) < _TOKEN_SOFT_MIN:
        print(
            f"[config] WARNING: transport.auth.token is shorter than "
            f"{_TOKEN_SOFT_MIN} chars — recommend regenerating with "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"',
            file=sys.stderr,
        )
    if auth_type == "none" and token:
        raise ValueError(
            "token configured but transport.auth.type=none — "
            "set type: bearer to enable it, or remove the token."
        )


def load_config(path: str = DEFAULT_CONFIG_PATH, *, required: bool = False) -> AppConfig:
    """Build the application config.

    The YAML file is optional. If it is absent and ``required`` is False, the
    config is assembled from environment variables and defaults. Pass
    ``required=True`` (server.py does this when ``--config`` is given
    explicitly) to error on a missing file the user asked for.
    """
    config_path = Path(path)
    raw: dict[str, Any] = {}

    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = _interpolate_dict(loaded)
    elif required:
        raise FileNotFoundError(f"Config file not found: {path}")

    token = _yaml_data.set(raw)
    try:
        config = AppConfig()
    finally:
        _yaml_data.reset(token)

    _validate_transport_auth(config.transport)
    return config
