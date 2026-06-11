"""Tests for the YAML config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from thousand_eyes_mcp.config import (  # noqa: F401 — verify all public symbols are exported
    DEFAULT_CONFIG_PATH,
    AppConfig,
    PaginationConfig,
    RetryConfig,
    ThousandEyesConfig,
    ThousandEyesMcpConfig,
    TransportAuthConfig,
    TransportConfig,
    load_config,
    resolve_config_path,
)

VALID_YAML = """\
thousand_eyes:
  base_url: https://api.thousandeyes.com/v7
  bearer_token: ${TE_TOKEN}
  account_group_id: ${TE_AID}
  verify_ssl: false
  timeout: 15.0
  retries:
    max_attempts: 5
    statuses: [502, 504]
    backoff_base: 0.25
    backoff_cap: 4.0
    retry_mutating: true

thousand_eyes_mcp:
  specs_dir: ./specs
  active_version: "7.0.88"
  max_actions_per_tool: 80
  auto_fetch: false
  pagination:
    enabled: true
    max_pages: 3
    page_size: 50

transport:
  mode: sse
  host: 0.0.0.0
  port: 9000
  auth:
    type: bearer
    token: ${MCP_TOKEN}
"""


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TE_TOKEN", "abcdef1234567890abcdef1234567890")
    monkeypatch.setenv("TE_AID", "12345")
    monkeypatch.setenv("MCP_TOKEN", "super-secret-token-32chars-min!!")
    path = tmp_path / "config.yaml"
    path.write_text(VALID_YAML)
    return path


def test_load_full_config(config_file: Path) -> None:
    cfg = load_config(str(config_file))
    assert isinstance(cfg, AppConfig)
    assert cfg.thousand_eyes.base_url == "https://api.thousandeyes.com/v7"
    assert cfg.thousand_eyes.bearer_token == "abcdef1234567890abcdef1234567890"
    assert cfg.thousand_eyes.account_group_id == "12345"
    assert cfg.thousand_eyes.timeout == 15.0
    assert cfg.thousand_eyes.retries.max_attempts == 5
    assert cfg.thousand_eyes.retries.statuses == (502, 504)
    assert cfg.thousand_eyes.retries.retry_mutating is True
    assert cfg.thousand_eyes_mcp.active_version == "7.0.88"
    assert cfg.thousand_eyes_mcp.max_actions_per_tool == 80
    assert cfg.thousand_eyes_mcp.auto_fetch is False
    assert cfg.thousand_eyes_mcp.pagination.max_pages == 3
    assert cfg.thousand_eyes_mcp.pagination.page_size == 50
    assert cfg.transport.mode == "sse"
    assert cfg.transport.host == "0.0.0.0"
    assert cfg.transport.port == 9000
    assert cfg.transport.auth.type == "bearer"
    assert cfg.transport.auth.token == "super-secret-token-32chars-min!!"


def test_defaults_applied_for_minimal_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("thousand_eyes:\n  bearer_token: short-but-ok\n")
    cfg = load_config(str(path))
    assert cfg.thousand_eyes.base_url == "https://api.thousandeyes.com/v7"
    assert cfg.thousand_eyes.verify_ssl is True
    assert cfg.thousand_eyes.timeout == 30.0
    assert cfg.thousand_eyes_mcp.active_version == "7.0.88"
    assert cfg.thousand_eyes_mcp.max_actions_per_tool == 80
    assert cfg.thousand_eyes_mcp.pagination.max_pages == 5
    assert cfg.thousand_eyes_mcp.auto_fetch is True
    assert cfg.transport.mode == "stdio"
    assert cfg.transport.auth.type == "none"


def test_missing_file_returns_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The YAML file is optional (#3): an absent file yields defaults + env."""
    for var in ("THOUSANDEYES_BEARER_TOKEN", "THOUSANDEYES_ACCOUNT_GROUP_ID"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config(str(tmp_path / "nope.yaml"))
    assert cfg.thousand_eyes.base_url == "https://api.thousandeyes.com/v7"
    assert cfg.thousand_eyes.bearer_token == ""
    assert cfg.thousand_eyes_mcp.active_version == "7.0.88"
    assert cfg.transport.mode == "stdio"


def test_missing_file_required_raises(tmp_path: Path) -> None:
    """When the user explicitly asks for a file (required=True), missing errors."""
    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "nope.yaml"), required=True)


def test_missing_env_var_substitutes_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("thousand_eyes:\n  bearer_token: ${DOES_NOT_EXIST}\n")
    cfg = load_config(str(path))
    assert cfg.thousand_eyes.bearer_token == ""
    captured = capsys.readouterr()
    assert "DOES_NOT_EXIST" in captured.err
    assert "WARNING" in captured.err


def test_bearer_requires_token(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "thousand_eyes:\n  bearer_token: x\ntransport:\n  auth:\n    type: bearer\n    token: ''\n"
    )
    with pytest.raises(ValueError, match="bearer requires a non-empty"):
        load_config(str(path))


def test_bearer_short_token_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "thousand_eyes:\n  bearer_token: x\n"
        "transport:\n  auth:\n    type: bearer\n    token: 'abc123'\n"  # 6 chars < 8
    )
    with pytest.raises(ValueError, match="too short"):
        load_config(str(path))


def test_bearer_soft_min_warning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "thousand_eyes:\n  bearer_token: x\n"
        "transport:\n  auth:\n    type: bearer\n    token: 'abcdefgh1234'\n"  # 12 < 16
    )
    load_config(str(path))
    captured = capsys.readouterr()
    assert "shorter than 16" in captured.err


def test_unknown_auth_type_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("thousand_eyes:\n  bearer_token: x\ntransport:\n  auth:\n    type: oauth2\n")
    # The Literal type now rejects unknown values during model construction
    # (pydantic.ValidationError is a subclass of ValueError).
    with pytest.raises(ValueError, match=r"transport\.auth\.type"):
        load_config(str(path))


def test_token_without_bearer_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "thousand_eyes:\n  bearer_token: x\n"
        "transport:\n  auth:\n    type: none\n    token: 'something'\n"
    )
    with pytest.raises(ValueError, match="type=none"):
        load_config(str(path))


def test_dataclasses_are_independent_instances() -> None:
    a = AppConfig()
    b = AppConfig()
    a.thousand_eyes.retries.statuses = (599,)
    assert b.thousand_eyes.retries.statuses == (429, 502, 503, 504)


def test_default_config_path_constant() -> None:
    assert DEFAULT_CONFIG_PATH == "thousand-eyes-mcp.yaml"


def test_resolve_returns_path_unchanged(tmp_path: Path) -> None:
    resolved, used_legacy = resolve_config_path(str(tmp_path / "x.yaml"), explicit=True)
    assert resolved == str(tmp_path / "x.yaml")
    assert used_legacy is False


# ---------------------------------------------------------------------------
# Env-first config (#3): env vars resolve the token with no YAML on disk.
# ---------------------------------------------------------------------------


def test_token_from_env_without_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Core scenario for #3: no config file, token from THOUSANDEYES_BEARER_TOKEN."""
    monkeypatch.setenv("THOUSANDEYES_BEARER_TOKEN", "tok-from-env")
    monkeypatch.setenv("THOUSANDEYES_ACCOUNT_GROUP_ID", "98765")
    cfg = load_config(str(tmp_path / "absent.yaml"))
    assert cfg.thousand_eyes.bearer_token == "tok-from-env"
    assert cfg.thousand_eyes.account_group_id == "98765"
    assert cfg.thousand_eyes.base_url == "https://api.thousandeyes.com/v7"


def test_env_overrides_yaml_but_preserves_other_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env vars win over YAML, but unspecified YAML fields are preserved (deep merge)."""
    monkeypatch.setenv("THOUSANDEYES_BEARER_TOKEN", "from-env")
    monkeypatch.delenv("THOUSANDEYES_ACCOUNT_GROUP_ID", raising=False)
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "thousand_eyes:\n  bearer_token: from-yaml\n  account_group_id: '4242'\n  timeout: 12.5\n"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.thousand_eyes.bearer_token == "from-env"  # env overrides
    assert cfg.thousand_eyes.account_group_id == "4242"  # YAML preserved
    assert cfg.thousand_eyes.timeout == 12.5  # YAML preserved


def test_bare_sections_fall_back_to_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare `thousand_eyes:` section (parses to None) must not crash."""
    monkeypatch.delenv("THOUSANDEYES_BEARER_TOKEN", raising=False)
    cfg = tmp_path / "c.yaml"
    cfg.write_text("thousand_eyes:\nthousand_eyes_mcp:\ntransport:\n")
    config = load_config(str(cfg))
    assert config.thousand_eyes.base_url == "https://api.thousandeyes.com/v7"
    assert config.thousand_eyes_mcp.active_version == "7.0.88"
    assert config.transport.mode == "stdio"


def test_verify_ssl_env_bool_coercion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """THOUSANDEYES_VERIFY_SSL=false must coerce to the bool False, not stay truthy.

    A silent ``True`` for ``VERIFY_SSL=false`` would be a security bug.
    """
    monkeypatch.setenv("THOUSANDEYES_BEARER_TOKEN", "tok")
    monkeypatch.setenv("THOUSANDEYES_VERIFY_SSL", "false")
    cfg = load_config(str(tmp_path / "absent.yaml"))
    assert cfg.thousand_eyes.verify_ssl is False
    monkeypatch.setenv("THOUSANDEYES_VERIFY_SSL", "true")
    cfg = load_config(str(tmp_path / "absent.yaml"))
    assert cfg.thousand_eyes.verify_ssl is True


def test_unquoted_numeric_yaml_coerced_to_str(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unquoted numeric YAML must coerce to str, matching the old loader (no crash).

    Numeric account-group IDs are idiomatic YAML; `active_version: 7.0` parses
    as a float. Both used to be wrapped in ``str(...)`` by the hand-rolled
    loader — the pydantic models must keep accepting them.
    """
    monkeypatch.delenv("THOUSANDEYES_ACCOUNT_GROUP_ID", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(
        "thousand_eyes:\n"
        "  bearer_token: x\n"
        "  account_group_id: 4242\n"
        "thousand_eyes_mcp:\n"
        "  active_version: 7.0\n"
    )
    cfg = load_config(str(path))
    assert cfg.thousand_eyes.account_group_id == "4242"
    assert cfg.thousand_eyes_mcp.active_version == "7.0"


def test_retry_null_statuses_falls_back(tmp_path: Path) -> None:
    """`statuses: ~` (YAML null) must fall back to defaults, not crash."""
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "thousand_eyes:\n  bearer_token: x\n  retries:\n    max_attempts: 5\n    statuses: ~\n"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.thousand_eyes.retries.max_attempts == 5
    assert cfg.thousand_eyes.retries.statuses == (429, 502, 503, 504)
