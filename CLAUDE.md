# CLAUDE.md

FastMCP server for the Cisco ThousandEyes API. Read-only by default.

## Stack

- Python ≥ 3.11. Managed with `uv`.
- `fastmcp` (≥ 2.0) for MCP protocol + transports (stdio, SSE, streamable-http).
- `httpx` async client for upstream calls.
- `pyyaml` for spec + config parsing.
- `python-dotenv` for `.env` loading.
- `starlette` for the bearer-token ASGI middleware on HTTP transports.

## Layout

```
thousand_eyes_mcp/
  __init__.py        — version constant
  server.py          — CLI entry point, transport wiring
  config.py          — YAML + ${ENV_VAR} loader, dataclass config tree
  auth.py            — bearer-token holder (no login flow)
  loader.py          — OpenAPI parser, action-name derivation, adaptive splitter
  dispatcher.py      — async httpx dispatch, retry, default-aid injection
  pagination.py      — cursor (_links.next) + offset/limit paginators
  tools.py           — FastMCP tool registration (one tool per ToolGroup)
  transport_auth.py  — bind-safety + bearer-token ASGI middleware
  diff.py            — version-diff utility
  fetcher/           — DevNet pubhub spec downloader + version discovery
  cli/               — `fetch`, `list-versions`, `discover-versions` subcommands
specs/
  <version>/<file>.{yaml,yml,json}   — one folder per version, merged on load
tests/                — pytest suite
docs/                 — mkdocs sources
.github/              — workflows, dependabot, issue templates
```

## Auth

ThousandEyes uses a long-lived OAuth2 bearer token issued from account
settings. There is no login endpoint. The dispatcher sets
`Authorization: Bearer <token>` on every request.

Generate a token at <https://app.thousandeyes.com> under
**Account Settings → Users and Roles → Profile → User API Tokens** and
export it as `THOUSANDEYES_BEARER_TOKEN` (or put it in `.env`).

Optional `THOUSANDEYES_ACCOUNT_GROUP_ID` is auto-injected as the `aid`
query param on every operation that declares one.

## Architecture (data flow)

1. `main()` parses CLI args; subcommand tokens short-circuit before server start.
2. `load_config()` reads `thousand-eyes-mcp.yaml`, resolves `${ENV_VAR}` references.
3. `_maybe_auto_fetch()` downloads the active spec from DevNet pubhub if needed.
4. `SpecLoader.load()` merges every spec file under `specs/<active_version>/`,
   extracts operations, runs the adaptive splitter, returns a `SpecIndex`.
5. `Dispatcher.connect()` verifies the bearer token is set.
6. `register_tools()` creates one MCP tool per `ToolGroup` with action-aware
   descriptions.
7. `mcp.run(...)` enters the transport loop.

Per-request:

- Tool handler validates `action` against the group's `valid_actions`, then
  delegates to `Dispatcher.call()`.
- Dispatcher detects pagination, drives the paginator if applicable, otherwise
  performs a single HTTP request with retry.
- Cursor pagination follows `_links.next.href` directly via the internal
  `_next_href` override; offset pagination increments `offset` until a short
  or empty page is observed.

## Splitter

See `docs/guides/tool-splitting.md`. Section → sub-tag → URL path depth
(3 → 4 → 5), with `<4`-op buckets collapsed into `<parent>_misc`. Default
cap 80; tune via `thousand_eyes_mcp.max_actions_per_tool` (0 disables).

## CLI

```
thousand-eyes-mcp                                 server (stdio default)
thousand-eyes-mcp --transport sse --host 0.0.0.0 --port 8000
thousand-eyes-mcp --read-write                    enable mutating endpoints
thousand-eyes-mcp --diff OLD NEW                  spec-version diff
thousand-eyes-mcp fetch <version>                 download a spec
thousand-eyes-mcp fetch --all-known               download every known version
thousand-eyes-mcp list-versions                   offline: known + cached
thousand-eyes-mcp discover-versions               experimental DevNet scrape
```

## Docker

`Dockerfile` is a multi-stage uv build. Specs are mounted at runtime
(not baked in) so version bumps don't require a rebuild. `docker-compose.yml`
runs SSE on `0.0.0.0:8000` by default.

## Key decisions

- **No login flow.** ThousandEyes tokens are long-lived; reactive 401
  handling falls back to the operator rotating the token.
- **Cursor follow.** `_links.next.href` is a fully-qualified URL — the
  paginator hands it back as `_next_href` and the dispatcher executes
  against it verbatim. This avoids re-parsing cursor tokens out of URLs.
- **TLS verification always on for spec downloads.** pubhub is a public CDN;
  `verify_ssl: false` on the upstream config never applies to fetch traffic.
- **Bind safety.** HTTP transports refuse `0.0.0.0` with `auth.type=none`
  unless `--insecure-allow-public` is passed.
- **Stable action names.** Derived from `(method, path, tag)` rather than
  upstream `operationId`, which can churn between releases. Cross-tool
  collisions get a path discriminator (`<bare>__<disc>`) rather than a
  numeric suffix.
