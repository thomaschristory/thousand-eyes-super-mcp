"""ThousandEyes upstream authentication.

ThousandEyes uses a long-lived OAuth2 bearer token issued out-of-band from
account settings. There is no login endpoint — the dispatcher simply sets
``Authorization: Bearer <token>`` on every request.

This module exposes a tiny ``ThousandEyesAuth`` class for API symmetry with
sibling projects that need full session/JWT lifecycle handling. ``login()``
is a no-op (validates the token is present); ``header()`` returns the
authorization header; ``needs_refresh()`` is always False.

All warnings/log lines route to stderr — stdout is reserved for the MCP
JSON-RPC stream on the default stdio transport.
"""

from __future__ import annotations

import sys

import httpx


class AuthError(RuntimeError):
    """Raised when the bearer token is missing or rejected."""


def require_credentials(bearer_token: str) -> None:
    """Raise a helpful error if the ThousandEyes bearer token is missing.

    Called early at startup (before spec loading) to fail fast.
    ``ThousandEyesAuth.login`` performs an equivalent inline check as
    defense-in-depth.
    """
    if not bearer_token:
        raise RuntimeError(
            "ThousandEyes bearer token is not set.\n"
            "Provide THOUSANDEYES_BEARER_TOKEN via either:\n"
            "  - an exported shell environment variable, or\n"
            "  - a .env file in the directory you run the command from "
            "(or next to --config).\n"
            "When launched by an MCP client (e.g. Claude Code), the client "
            "does not inherit your shell exports — set it in the client's "
            "server `env` block instead.\n"
            "Generate a token at https://app.thousandeyes.com under "
            "Account Settings → Users and Roles → Profile → User API Tokens."
        )


class ThousandEyesAuth:
    def __init__(self, *, bearer_token: str) -> None:
        self._token = bearer_token

    async def login(self, client: httpx.AsyncClient) -> None:
        """No-op login; verifies the bearer token is set.

        Mirrors the sibling-project surface area. The ``client`` argument is
        accepted to keep the contract stable, but no HTTP request is made.
        """
        del client  # unused
        if not self._token:
            raise AuthError(
                "ThousandEyes bearer token is not set. "
                "Set THOUSANDEYES_BEARER_TOKEN in your .env file. "
                "Generate one at https://app.thousandeyes.com under "
                "Account Settings → Users and Roles → Profile → User API Tokens."
            )
        print("[auth] ThousandEyes bearer token configured", file=sys.stderr)

    def header(self) -> dict[str, str]:
        """Return the Authorization header for authenticated requests."""
        if not self._token:
            raise AuthError("Not authenticated — call login() first")
        return {"Authorization": f"Bearer {self._token}"}

    def expires_in(self) -> float | None:
        """Bearer tokens are long-lived and opaque — no exp claim to inspect."""
        return None

    def needs_refresh(self, margin_seconds: int = 120) -> bool:
        """Bearer tokens never need proactive refresh."""
        del margin_seconds
        return False
