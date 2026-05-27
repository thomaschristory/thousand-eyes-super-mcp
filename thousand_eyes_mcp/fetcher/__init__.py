"""Download ThousandEyes OpenAPI specs from Cisco DevNet pubhub.

URLs are hardcoded per known version. Unknown versions raise
SpecVersionUnknownError with an actionable message pointing the user at
https://developer.cisco.com/docs/thousandeyes/ and to KNOWN_SPEC_URLS so
they can add a new entry.

The server invokes this at startup when
thousand_eyes_mcp.auto_fetch is true and the version directory is empty.

Security note — TLS verification:
    The downloads target pubhub.devnetcloud.com, a public HTTPS CDN. This
    fetcher ALWAYS verifies TLS (`verify=True`) regardless of what the
    upstream config says about `verify_ssl`. Disabling verification
    here would open a MITM vector to inject a malicious spec.
"""

from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

# Currently published unified ThousandEyes OpenAPI spec on DevNet.
# Update by appending a new entry when a new version is needed.
KNOWN_SPEC_URLS: dict[str, str] = {
    "7.0.88": "https://pubhub.devnetcloud.com/media/000-v7-apis/docs/reference/unified-oas/api.yaml",
}


class SpecVersionUnknownError(RuntimeError):
    """Raised when fetch_spec is asked for a version not in KNOWN_SPEC_URLS."""


class SpecContentInvalidError(RuntimeError):
    """Raised when the downloaded body parses as YAML/JSON but isn't an OpenAPI/Swagger spec."""


def _url_filename(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    return name or "api.yaml"


def _validate_spec_shape(parsed: object, url: str, raw: bytes) -> None:
    """Ensure parsed content looks like an OpenAPI 3.x or Swagger 2.0 document."""
    snippet = raw[:200].decode("utf-8", errors="replace")
    if not isinstance(parsed, dict):
        raise SpecContentInvalidError(
            f"Downloaded body from {url} parsed but is not a dict "
            f"(got {type(parsed).__name__}). First 200 chars: {snippet!r}. "
            f"Check whether pubhub rotated the URL."
        )
    if "openapi" not in parsed and "swagger" not in parsed:
        raise SpecContentInvalidError(
            f"Downloaded body from {url} has no 'openapi' or "
            f"'swagger' top-level key. First 200 chars: {snippet!r}. "
            f"Check whether pubhub rotated the URL."
        )
    if "paths" not in parsed:
        raise SpecContentInvalidError(
            f"Downloaded body from {url} has no 'paths' top-level "
            f"key. First 200 chars: {snippet!r}. "
            f"Check whether pubhub rotated the URL."
        )


async def fetch_spec(
    version: str,
    dest_dir: Path,
    *,
    client: httpx.AsyncClient | None = None,
) -> Path:
    """Download the ThousandEyes OpenAPI spec for ``version`` into ``dest_dir``.

    Returns the path of the written file. Raises SpecVersionUnknownError
    for unknown versions, SpecContentInvalidError if pubhub returned 200 but
    not an OpenAPI document, or propagates httpx errors. No partial file is
    left behind on any failure.

    TLS verification is always on. See module docstring.
    """
    url = KNOWN_SPEC_URLS.get(version)
    if url is None:
        supported = ", ".join(sorted(KNOWN_SPEC_URLS))
        raise SpecVersionUnknownError(
            f"No known download URL for ThousandEyes version '{version}'. "
            f"Supported: {supported}. "
            f"To add a new version, find its download URL at "
            f"https://developer.cisco.com/docs/thousandeyes/ and append it to "
            f"thousand_eyes_mcp/fetcher/__init__.py:KNOWN_SPEC_URLS."
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / _url_filename(url)
    tmp = final.with_suffix(final.suffix + ".tmp")

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(verify=True, timeout=120.0, follow_redirects=True)

    try:
        print(f"[fetcher] Downloading {url}", file=sys.stderr)
        try:
            response = await client.get(url)
            response.raise_for_status()
            tmp.write_bytes(response.content)
            loader_cls: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
            parsed = yaml.load(tmp.read_bytes(), Loader=loader_cls)
            _validate_spec_shape(parsed, url, response.content)
        except Exception:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        tmp.rename(final)
        print(f"[fetcher] Wrote {final} ({final.stat().st_size} bytes)", file=sys.stderr)
        return final
    finally:
        if owns_client:
            await client.aclose()


@dataclass(frozen=True)
class VersionInfo:
    """One row in ``list-versions`` output."""

    version: str
    cached: bool
    extra: bool  # True when this dir is not in KNOWN_SPEC_URLS


def _has_spec_files(version_dir: Path) -> bool:
    if not version_dir.is_dir():
        return False
    return any(any(version_dir.glob(f"*.{ext}")) for ext in ("yaml", "yml", "json"))


def list_known_versions(specs_dir: Path) -> list[VersionInfo]:
    """Return KNOWN_SPEC_URLS + any extra on-disk version dirs, with cache state."""
    if not specs_dir.is_dir():
        print(
            f"[fetcher] WARNING: specs_dir '{specs_dir}' does not exist; "
            f"showing only hardcoded versions as uncached.",
            file=sys.stderr,
        )
    known = set(KNOWN_SPEC_URLS)
    seen: set[str] = set()
    out: list[VersionInfo] = []

    for v in sorted(known):
        cached = _has_spec_files(specs_dir / v)
        out.append(VersionInfo(version=v, cached=cached, extra=False))
        seen.add(v)

    if specs_dir.is_dir():
        for sub in sorted(specs_dir.iterdir()):
            if not sub.is_dir() or sub.name in seen:
                continue
            if _has_spec_files(sub):
                out.append(VersionInfo(version=sub.name, cached=True, extra=True))

    return out
