"""Tests for the spec-diff utility."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from thousand_eyes_mcp.diff import diff_versions, print_diff


def test_diff_added_and_removed(tmp_path: Path, minimal_specs_dir: Path) -> None:
    # Create a second version with one extra path and one missing path.
    src = minimal_specs_dir / "7.0.88" / "te-min.json"
    spec = json.loads(src.read_text())
    v2_dir = minimal_specs_dir / "7.0.89"
    v2_dir.mkdir()
    spec2 = dict(spec)
    spec2["paths"] = dict(spec["paths"])
    # Remove /tests and add /new-thing
    del spec2["paths"]["/tests"]
    spec2["paths"]["/new-thing"] = {
        "get": {"tags": ["Things"], "operationId": "getNewThing", "responses": {"200": {}}}
    }
    (v2_dir / "te-min.json").write_text(json.dumps(spec2))

    diff = diff_versions(str(minimal_specs_dir), "7.0.88", "7.0.89", read_write=True)
    assert any(op.operation_id == "getAllTests" for op in diff.removed)
    assert any(op.operation_id == "getNewThing" for op in diff.added)


def test_print_diff_no_crash(minimal_specs_dir: Path, tmp_path: Path) -> None:
    # Diff a version against itself: empty diff, just exercises the formatter.
    shutil.copytree(minimal_specs_dir / "7.0.88", minimal_specs_dir / "7.0.88b")
    diff = diff_versions(str(minimal_specs_dir), "7.0.88", "7.0.88b", read_write=True)
    print_diff(diff)
