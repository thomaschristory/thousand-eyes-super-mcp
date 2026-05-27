"""Shared pytest fixtures for the thousand-eyes-mcp test suite."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

_FIXTURE_SPEC = Path(__file__).parent / "fixtures" / "specs" / "7.0.88" / "te-min.json"


@pytest.fixture
def minimal_specs_dir(tmp_path: Path) -> Path:
    """A specs/ directory containing a minimal ThousandEyes-shaped 7.0.88 spec."""
    dest = tmp_path / "specs" / "7.0.88"
    dest.mkdir(parents=True)
    shutil.copy(_FIXTURE_SPEC, dest / "te-min.json")
    return tmp_path / "specs"


@pytest.fixture
def minimal_spec_dict() -> dict:
    """The raw fixture spec as a Python dict — for unit tests that don't need disk I/O."""
    return json.loads(_FIXTURE_SPEC.read_text())
