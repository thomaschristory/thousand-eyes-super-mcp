"""Shared helpers for cli subcommands."""

from __future__ import annotations

import argparse
import sys

from thousand_eyes_mcp.config import (
    DEFAULT_CONFIG_PATH,
    AppConfig,
    load_config,
    resolve_config_path,
)


def add_config_args(parser: argparse.ArgumentParser) -> None:
    """Append the shared --config / --specs-dir options to a subcommand parser."""
    parser.add_argument(
        "--config",
        default=None,
        help=f"path to the config file (default: ./{DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--specs-dir",
        default=None,
        help="override thousand_eyes_mcp.specs_dir from the config file",
    )


def load_config_or_default(config_arg: str | None) -> AppConfig:
    """Resolve and load the config. Honour explicit --config strictly.

    If --config was passed explicitly and the path doesn't exist, raise a
    SystemExit(2) with an actionable stderr message — do NOT silently fall
    back to AppConfig() defaults (that hides user typos).
    """
    explicit = config_arg is not None
    resolved, _ = resolve_config_path(config_arg or DEFAULT_CONFIG_PATH, explicit=explicit)
    try:
        return load_config(resolved, required=explicit)
    except FileNotFoundError:
        if explicit:
            print(
                f"[cli] ERROR: --config '{resolved}' not found. "
                f"Check the path or omit --config to use the default.",
                file=sys.stderr,
            )
            raise SystemExit(2) from None
        return AppConfig()
