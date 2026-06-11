"""FastMCP server entry point for ThousandEyes Super MCP.

``main()`` is the ``[project.scripts]`` target declared in pyproject.toml. It
wires together config loading, spec loading, upstream auth, dispatcher, tool
registration, and transport selection (stdio / sse / streamable-http).

All non-MCP log lines route to stderr — stdout is reserved for the JSON-RPC
stream when running on stdio transport.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Literal, cast

import httpx
from dotenv import find_dotenv, load_dotenv
from fastmcp import FastMCP
from starlette.middleware import Middleware

from . import __version__
from .auth import ThousandEyesAuth, require_credentials
from .config import DEFAULT_CONFIG_PATH, AppConfig, load_config
from .diff import diff_versions, print_diff
from .dispatcher import Dispatcher
from .fetcher import (
    SpecContentInvalidError,
    SpecVersionUnknownError,
    fetch_spec,
)
from .loader import SpecLoader
from .tools import register_tools
from .transport_auth import BearerAuthMiddleware, decide_bind

_VALID_TRANSPORTS: frozenset[str] = frozenset({"stdio", "sse", "streamable-http"})
_SUBCOMMANDS: frozenset[str] = frozenset({"fetch", "list-versions", "discover-versions"})
TransportMode = Literal["stdio", "sse", "streamable-http"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="thousand-eyes-mcp",
        description=(
            "FastMCP server for Cisco ThousandEyes, dynamically generated from the OpenAPI spec."
        ),
        epilog=(
            "Subcommands (run with --help for details):\n"
            "  fetch              Download an OpenAPI spec for one or all known versions.\n"
            "  list-versions      List known + on-disk versions (offline).\n"
            "  discover-versions  [experimental] Diff DevNet vs KNOWN_SPEC_URLS.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=None,
        help=f"path to the config file (default: ./{DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--transport",
        choices=sorted(_VALID_TRANSPORTS),
        default=None,
        help="override transport.mode from the config file",
    )
    parser.add_argument("--host", default=None, help="override transport.host")
    parser.add_argument("--port", type=int, default=None, help="override transport.port")
    parser.add_argument(
        "--read-write",
        action="store_true",
        help="register POST/PUT/DELETE/PATCH endpoints (read-only by default)",
    )
    parser.add_argument(
        "--version",
        dest="version",
        default=None,
        help="override thousand_eyes_mcp.active_version",
    )
    parser.add_argument(
        "--max-actions-per-tool",
        type=int,
        default=None,
        help="override the adaptive splitter cap (0 disables splitting)",
    )
    parser.add_argument(
        "--insecure-allow-public",
        action="store_true",
        help="permit binding 0.0.0.0 with transport.auth.type=none (NOT recommended)",
    )
    parser.add_argument(
        "--diff",
        nargs=2,
        metavar=("OLD", "NEW"),
        default=None,
        help="diff two spec versions and exit",
    )
    parser.add_argument(
        "--show-version",
        action="version",
        version=f"thousand-eyes-mcp {__version__}",
    )
    return parser.parse_args(argv)


def _load_env(config_path: str | None = None) -> None:
    """Load a ``.env`` so ``${VAR}`` interpolation and credentials resolve.

    python-dotenv's bare ``load_dotenv()`` searches upward from *this module's*
    directory. Once the package is installed (``uv tool install`` / pipx), that
    is site-packages — so a ``.env`` in the user's project dir is never found
    (#3). We instead search the current working directory, and additionally
    next to the ``--config`` file when one is given. Already-exported shell
    variables always win (``override=False``).
    """
    # .env in the cwd (or any parent) — the usual "run it from my project" case.
    cwd_env = find_dotenv(usecwd=True)
    if cwd_env:
        load_dotenv(cwd_env)
    # .env beside the config file — covers `--config /elsewhere/thousand-eyes-mcp.yaml`.
    if config_path:
        cfg_env = Path(config_path).resolve().parent / ".env"
        if cfg_env.is_file():
            load_dotenv(cfg_env)


def _load_config_or_default(config_path: str | None) -> AppConfig:
    """Load config if present; otherwise return defaults (diff/fetch need no token)."""
    return load_config(config_path or DEFAULT_CONFIG_PATH, required=config_path is not None)


def run_diff(specs_dir: str, old_version: str, new_version: str) -> int:
    diff = diff_versions(specs_dir, old_version, new_version, read_write=True)
    print_diff(diff)
    return 0


async def _maybe_auto_fetch(
    *,
    auto_fetch: bool,
    specs_dir: Path,
    version: str,
) -> None:
    """Download the spec for ``version`` into ``specs_dir/<version>/`` if needed."""
    version_dir = specs_dir / version
    has_specs = version_dir.exists() and (
        any(version_dir.glob("*.yaml"))
        or any(version_dir.glob("*.yml"))
        or any(version_dir.glob("*.json"))
    )
    if not auto_fetch:
        if not has_specs:
            print(
                f"[server] WARNING: auto_fetch is disabled and "
                f"{version_dir}/ has no spec files. Either set "
                f"auto_fetch: true in thousand-eyes-mcp.yaml, or download the spec "
                f"manually from Cisco DevNet to that directory.",
                file=sys.stderr,
            )
        return
    if has_specs:
        return
    print(
        f"[server] auto_fetch enabled — downloading spec for {version}",
        file=sys.stderr,
    )
    try:
        await fetch_spec(version, version_dir)
    except (SpecVersionUnknownError, SpecContentInvalidError, httpx.HTTPError) as exc:
        raise RuntimeError(
            f"[startup] auto-fetch failed for version {version}: {exc}. "
            f"Set auto_fetch: false in thousand-eyes-mcp.yaml and place the spec "
            f"manually under {version_dir}/, or fix the upstream issue."
        ) from exc


async def _connect_and_register(
    args: argparse.Namespace,
) -> tuple[FastMCP, Dispatcher, TransportMode, str, int, list[Middleware]]:
    _load_env(args.config)
    config = load_config(args.config or DEFAULT_CONFIG_PATH, required=args.config is not None)

    # Fail fast: spec loading (and auto-fetch) is pointless without a token.
    require_credentials(config.thousand_eyes.bearer_token)

    version = args.version or config.thousand_eyes_mcp.active_version
    transport = args.transport or config.transport.mode
    if transport not in _VALID_TRANSPORTS:
        raise ValueError(
            f"Unsupported transport: {transport!r}. Choose one of {sorted(_VALID_TRANSPORTS)}."
        )
    transport_mode = cast(TransportMode, transport)
    host = args.host or config.transport.host
    port = args.port or config.transport.port
    read_write = args.read_write
    max_actions = (
        args.max_actions_per_tool
        if args.max_actions_per_tool is not None
        else config.thousand_eyes_mcp.max_actions_per_tool
    )

    middleware_list: list[Middleware] = []
    if transport_mode != "stdio":
        effective_host, warnings = decide_bind(
            host=host,
            auth_type=config.transport.auth.type,
            insecure_ok=args.insecure_allow_public,
        )
        for line in warnings:
            print(f"[server] WARNING: {line}", file=sys.stderr)
        host = effective_host
        if config.transport.auth.type == "bearer":
            middleware_list.append(
                Middleware(BearerAuthMiddleware, expected_token=config.transport.auth.token)
            )

    print(
        f"[server] ThousandEyes Super MCP v{__version__} — "
        f"version={version}, RO={'no' if read_write else 'yes'}, transport={transport_mode}",
        file=sys.stderr,
    )

    await _maybe_auto_fetch(
        auto_fetch=config.thousand_eyes_mcp.auto_fetch,
        specs_dir=Path(config.thousand_eyes_mcp.specs_dir),
        version=version,
    )

    index = SpecLoader(
        config.thousand_eyes_mcp.specs_dir,
        version,
        read_write=read_write,
        max_actions_per_tool=max_actions,
    ).load()

    auth = ThousandEyesAuth(bearer_token=config.thousand_eyes.bearer_token)
    dispatcher = Dispatcher(
        base_url=config.thousand_eyes.base_url,
        auth=auth,
        verify_ssl=config.thousand_eyes.verify_ssl,
        timeout=config.thousand_eyes.timeout,
        pagination=config.thousand_eyes_mcp.pagination,
        retry=config.thousand_eyes.retries,
        default_account_group_id=config.thousand_eyes.account_group_id,
    )
    await dispatcher.connect()
    dispatcher.set_index(index)

    mcp = FastMCP("thousand-eyes-mcp")
    register_tools(mcp, index, dispatcher)
    return mcp, dispatcher, transport_mode, host, port, middleware_list


def build_and_run(args: argparse.Namespace) -> int:
    mcp, dispatcher, transport_mode, host, port, middleware = asyncio.run(
        _connect_and_register(args)
    )
    try:
        if transport_mode == "stdio":
            mcp.run()
        else:
            mcp.run(
                transport=transport_mode,
                host=host,
                port=port,
                middleware=middleware,
            )
    finally:
        asyncio.run(dispatcher.close())
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw and raw[0] in _SUBCOMMANDS:
        sub, rest = raw[0], raw[1:]
        if sub == "fetch":
            from .cli.fetch import run_fetch

            return run_fetch(rest)
        if sub == "list-versions":
            from .cli.list_versions import run_list_versions

            return run_list_versions(rest)
        if sub == "discover-versions":
            from .cli.discover import run_discover_versions

            return run_discover_versions(rest)

    args = parse_args(raw)

    if args.diff:
        _load_env(args.config)
        config = _load_config_or_default(args.config)
        old, new = args.diff
        return run_diff(config.thousand_eyes_mcp.specs_dir, old, new)

    return build_and_run(args)


if __name__ == "__main__":
    sys.exit(main())
