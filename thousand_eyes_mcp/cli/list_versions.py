"""``thousand-eyes-mcp list-versions`` subcommand: enumerate known + on-disk specs."""

from __future__ import annotations

import argparse
from pathlib import Path

from thousand_eyes_mcp.cli._common import add_config_args, load_config_or_default
from thousand_eyes_mcp.fetcher import KNOWN_SPEC_URLS, list_known_versions


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thousand-eyes-mcp list-versions",
        description="List spec versions known to this build and any cached on disk.",
    )
    add_config_args(parser)
    return parser


def run_list_versions(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)
    config = load_config_or_default(args.config)
    specs_dir = Path(args.specs_dir or config.thousand_eyes_mcp.specs_dir)

    print("Known versions (hardcoded in KNOWN_SPEC_URLS):")
    for v in sorted(KNOWN_SPEC_URLS):
        print(f"  {v}")

    print()
    print(f"Versions on disk under {specs_dir}/:")
    rows = list_known_versions(specs_dir)
    if not rows:
        print("  (none)")
    else:
        width = max(len(r.version) for r in rows)
        for r in rows:
            tag_parts = []
            if r.cached:
                tag_parts.append("cached")
            if r.extra:
                tag_parts.append("extra")
            tag = ", ".join(tag_parts) if tag_parts else "-"
            print(f"  {r.version:<{width}}  ({tag})")

    return 0
