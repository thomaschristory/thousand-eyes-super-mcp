"""``thousand-eyes-mcp discover-versions`` subcommand.

[Experimental] Scrapes DevNet's docs landing page and prints a diff vs the
hardcoded ``KNOWN_SPEC_URLS`` table. Useful for spotting new releases without
manual page-watching. Does not mutate the hardcoded table.

Exit codes:
  0  -- every hardcoded entry was found on DevNet (no stale entries).
        New entries on DevNet that are not in KNOWN_SPEC_URLS are surfaced
        as ``+`` lines but do not change the exit code.
  1  -- one or more hardcoded entries are no longer visible on DevNet.
        The hardcoded table may be stale.
  2  -- DiscoveryError (page shape changed / regex matched nothing) or
        an httpx error (network down, non-2xx response).
"""

from __future__ import annotations

import argparse
import sys

import httpx

from thousand_eyes_mcp.fetcher import KNOWN_SPEC_URLS
from thousand_eyes_mcp.fetcher.discover import (
    DEVNET_INDEX_URL,
    DiscoveryError,
    discover_versions,
)


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="thousand-eyes-mcp discover-versions",
        description=(
            "[experimental] Print a diff of DevNet-discoverable ThousandEyes "
            "spec versions vs the hardcoded KNOWN_SPEC_URLS table. DevNet's HTML "
            "shape can change without notice; the helper raises a clear error and "
            "exits 2 when the regex matches nothing."
        ),
    )


def run_discover_versions(argv: list[str]) -> int:
    _build_parser().parse_args(argv)
    try:
        discovered = discover_versions()
    except DiscoveryError as exc:
        print(f"[discover] {exc}", file=sys.stderr)
        return 2
    except httpx.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        print(
            f"[discover] DevNet unreachable at {DEVNET_INDEX_URL} "
            f"({type(exc).__name__}, status={status}): {exc}",
            file=sys.stderr,
        )
        return 2

    # Discovered keys are major-only (e.g. "v7"). Compare against the major
    # extracted from each hardcoded version (e.g. "7.0.88" → "v7").
    known_majors = {f"v{v.split('.', 1)[0]}": v for v in KNOWN_SPEC_URLS}
    all_keys = sorted(set(discovered) | set(known_majors))
    stale = False
    print(
        f"Diff vs KNOWN_SPEC_URLS ({len(KNOWN_SPEC_URLS)} hardcoded, {len(discovered)} discovered):"
    )
    for v in all_keys:
        in_disco = v in discovered
        in_known = v in known_majors
        if in_disco and in_known:
            print(f"  = {v}  (hardcoded as {known_majors[v]})")
        elif in_disco and not in_known:
            print(f"  + {v}    (new on DevNet -- consider adding to KNOWN_SPEC_URLS)")
        else:
            stale = True
            print(f"  - {v}    (in KNOWN_SPEC_URLS but NOT on DevNet -- possibly stale)")
    return 1 if stale else 0
