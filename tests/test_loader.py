"""Tests for the SpecLoader."""

from __future__ import annotations

from pathlib import Path

from thousand_eyes_mcp.loader import (
    RO_METHODS,
    RW_METHODS,
    SpecLoader,
)


def test_loader_builds_index(minimal_specs_dir: Path) -> None:
    loader = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False)
    index = loader.load()
    # Read-only mode: only GETs survive
    assert all(op.method == "get" for op in index.by_action_name.values())
    assert "get_tests" in index.by_action_name or any(
        "tests" in name for name in index.by_action_name
    )


def test_loader_rw_includes_mutating(minimal_specs_dir: Path) -> None:
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=True).load()
    methods = {op.method for op in index.by_action_name.values()}
    assert "post" in methods


def test_loader_missing_version_dir_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(FileNotFoundError, match="Spec directory not found"):
        SpecLoader(str(tmp_path / "specs"), "1.0.0")


def test_loader_detects_cursor_pagination(minimal_specs_dir: Path) -> None:
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=False).load()
    alerts_ops = [
        op for op in index.by_action_name.values() if op.tag == "Alerts" and op.method == "get"
    ]
    assert alerts_ops
    assert alerts_ops[0].pagination == "cursor"


def test_method_constants_sane() -> None:
    assert "get" in RO_METHODS
    assert "post" not in RO_METHODS
    assert RO_METHODS <= RW_METHODS
