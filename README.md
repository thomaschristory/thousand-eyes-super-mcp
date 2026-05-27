# thousand-eyes-super-mcp

FastMCP server for the Cisco ThousandEyes API, generated dynamically from
the official OpenAPI spec.

ThousandEyes is Cisco's SaaS for end-to-end network and application
visibility — synthetic agent tests, BGP monitoring, internet weather,
endpoint experience. This server exposes the entire v7 REST API as MCP
tools, ready to drop into Claude Desktop, Claude Code, Cursor, or any
MCP-aware client.

## Status

Read-only by default. See [`CHANGELOG.md`](CHANGELOG.md) for the current
release and what changed.

## Install

```bash
uv tool install thousand-eyes-super-mcp
```

## Configure

1. Copy `.env.example` to `.env` and fill in `THOUSANDEYES_BEARER_TOKEN`.
   Generate a token at <https://app.thousandeyes.com> under
   **Account Settings → Users and Roles → Profile → User API Tokens**.
2. (Optional) Edit `thousand-eyes-mcp.yaml` for runtime knobs — transport,
   splitting cap, retries, pagination, default account group.

## Run

```bash
thousand-eyes-mcp                                          # stdio (Claude Desktop)
thousand-eyes-mcp --transport sse --host 0.0.0.0 --port 8000
thousand-eyes-mcp --read-write                             # enable mutating endpoints
```

## CLI subcommands

```bash
thousand-eyes-mcp fetch 7.0.88              # download a spec without starting the server
thousand-eyes-mcp fetch --all-known         # download every known version
thousand-eyes-mcp list-versions             # offline: enumerate known + cached versions
thousand-eyes-mcp discover-versions         # [experimental] diff DevNet vs KNOWN_SPEC_URLS
thousand-eyes-mcp --diff 7.0.88 7.0.89      # diff two on-disk spec versions
```

## How it works

- **Bearer token auth.** ThousandEyes issues long-lived OAuth2 bearer tokens
  out-of-band; the dispatcher sets `Authorization: Bearer <token>` on every
  request. No login flow, no refresh.
- **Dynamic tool generation.** At startup, the loader merges every spec file
  under `specs/<version>/`, groups operations by tag, and splits oversized
  groups via an adaptive algorithm (section → sub-tag → URL path depth) so
  no single tool overwhelms the LLM with too many actions.
- **Stable action names.** Derived from `(method, path, tag)` rather than
  upstream `operationId`, so naming stays consistent across spec releases.
- **Pagination auto-follow.** Cursor pagination via `_links.next.href` and
  offset/limit pagination are detected from spec parameters and stitched
  automatically. Override per-call with reserved params `_max_pages`,
  `_page_size`, `_auto_follow`.
- **Configurable retry.** 429/502/503/504 are retried with exponential
  backoff + jitter. Mutating methods are not retried by default; opt in
  with `retry_mutating: true`.
- **Transports.** stdio (default), SSE, streamable-http. HTTP transports
  refuse to bind a non-loopback host with `auth.type=none` unless
  `--insecure-allow-public` is passed.

## Docker

```bash
docker compose up --build
```

## License

Apache-2.0 — see [`LICENSE`](LICENSE).
