"""Discover ThousandEyes spec versions by scraping DevNet's docs index.

The DevNet docs page at ``https://developer.cisco.com/docs/thousandeyes/`` is
JS-driven; this helper attempts a regex pass against the static HTML and
raises ``DiscoveryError`` when zero URLs are extracted, so the failure is
loud rather than silent.

Known limitation:
    The live DevNet landing page is largely a JS SPA. In practice the regex
    below may match zero URLs against the live page, which intentionally
    raises ``DiscoveryError``. The maintainer should then inspect the page
    manually and update ``KNOWN_SPEC_URLS`` in
    ``thousand_eyes_mcp/fetcher/__init__.py``. The regex IS exercised by a
    synthetic-HTML test suite so the parser stays correct should DevNet
    publish a static, fully-linked index in future.

Network usage:
    ``discover_versions()`` makes one HTTPS request to ``DEVNET_INDEX_URL``.
    TLS verification is always on — DevNet is a public CDN, MITM risk
    doesn't depend on any upstream config.
"""

from __future__ import annotations

import re
import sys
from typing import Final

import httpx

DEVNET_INDEX_URL: Final[str] = "https://developer.cisco.com/docs/thousandeyes/"


class DiscoveryError(RuntimeError):
    """Raised when the DevNet page contains no extractable spec links."""


# Matches: https://pubhub.devnetcloud.com/media/<some-slug>-v<ver-snake>-apis/.../api.{yaml,json}
_SPEC_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"https://pubhub\.devnetcloud\.com/media/"
    r"(?P<slug>[0-9a-zA-Z\-]+)"
    r"/docs/reference/unified-oas/api\.(?:yaml|json)"
)

# Valid slugs end in ``-v<digits>-apis`` (e.g. "000-v7-apis").
_VERSION_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-zA-Z]+-v(?P<major>\d+)-apis$")


def parse_discovery_html(html: str) -> dict[str, str]:
    """Extract ``{version: url}`` from DevNet's docs HTML.

    Raises ``DiscoveryError`` when no matches are found — the strongest
    signal the SPA shape has changed.

    The slug only carries the major version (e.g. ``v7``); the minor/patch
    is embedded inside the spec body itself, so this helper reports
    ``"v<major>"`` and leaves resolution to the maintainer.
    """
    out: dict[str, str] = {}
    for match in _SPEC_URL_RE.finditer(html):
        slug = match.group("slug")
        sm = _VERSION_SLUG_RE.match(slug)
        if not sm:
            print(
                f"[discover] WARNING: skipping non-version slug {slug!r}",
                file=sys.stderr,
            )
            continue
        version = f"v{sm.group('major')}"
        existing = out.get(version)
        if existing is None:
            out[version] = match.group(0)
        elif existing != match.group(0):
            print(
                f"[discover] WARNING: duplicate URLs for {version!r} "
                f"(keeping first: {existing}, ignoring: {match.group(0)})",
                file=sys.stderr,
            )
    if not out:
        raise DiscoveryError(
            f"Found no spec links matching the pubhub URL pattern on the DevNet page. "
            f"The page's HTML shape may have changed. Inspect "
            f"{DEVNET_INDEX_URL} manually and update the regex in "
            f"thousand_eyes_mcp/fetcher/discover.py."
        )
    return out


def discover_versions() -> dict[str, str]:
    """Fetch DevNet's docs index page and return ``{version: pubhub_url}``."""
    with httpx.Client(verify=True, timeout=30.0, follow_redirects=True) as client:
        response = client.get(DEVNET_INDEX_URL)
        response.raise_for_status()
        if str(response.url).rstrip("/") != DEVNET_INDEX_URL.rstrip("/"):
            print(
                f"[discover] WARNING: followed redirect to {response.url} "
                f"(expected {DEVNET_INDEX_URL}). Auth wall? Page moved?",
                file=sys.stderr,
            )
        return parse_discovery_html(response.text)
