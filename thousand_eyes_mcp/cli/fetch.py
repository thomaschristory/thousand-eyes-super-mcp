"""``thousand-eyes-mcp fetch`` subcommand: download an OpenAPI spec by version."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

from thousand_eyes_mcp.cli._common import add_config_args, load_config_or_default
from thousand_eyes_mcp.fetcher import (
    KNOWN_SPEC_URLS,
    SpecContentInvalidError,
    SpecVersionUnknownError,
    fetch_spec,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thousand-eyes-mcp fetch",
        description="Download an OpenAPI spec for a ThousandEyes version.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "version",
        nargs="?",
        default=None,
        help="version to fetch (e.g. 7.0.88). Mutually exclusive with --all-known.",
    )
    group.add_argument(
        "--all-known",
        action="store_true",
        help="fetch every version known to this build (run 'list-versions' to see them).",
    )
    add_config_args(parser)
    return parser


def run_fetch(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    config = load_config_or_default(args.config)
    specs_dir = Path(args.specs_dir or config.thousand_eyes_mcp.specs_dir)

    versions: list[str] = list(KNOWN_SPEC_URLS) if args.all_known else [args.version]

    async def _runner() -> int:
        succeeded: list[str] = []
        failed: list[str] = []
        for v in versions:
            try:
                target = await fetch_spec(v, specs_dir / v)
                print(f"[fetch] OK  {v} -> {target}", file=sys.stderr)
                succeeded.append(v)
            except (
                SpecVersionUnknownError,
                SpecContentInvalidError,
                httpx.HTTPError,
                json.JSONDecodeError,
                OSError,
            ) as exc:
                print(
                    f"[fetch] FAIL {v} ({type(exc).__name__}): {exc}",
                    file=sys.stderr,
                )
                failed.append(v)
        if len(versions) > 1:
            print(
                f"[fetch] {len(succeeded)}/{len(versions)} succeeded"
                + (f", failed: {', '.join(failed)}" if failed else ""),
                file=sys.stderr,
            )
        return 1 if failed else 0

    return asyncio.run(_runner())
