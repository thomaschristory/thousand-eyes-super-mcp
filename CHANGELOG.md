# Changelog

All notable changes to thousand-eyes-super-mcp will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Body-bearing `post_*`/`put_*`/`patch_*` actions now advertise their real
  top-level request-body fields instead of an opaque `body: object`, and the
  dispatcher defensively unwraps a lone `{"body": {...}}` so callers that
  followed the old convention still succeed** (#9). The tool description now
  states once (only on tools that have a body-bearing action) that body fields
  go at the **top level** of `params`. The loader resolves the `requestBody`
  schema (following `$ref`, merging `allOf`) to extract field names, types,
  descriptions and defaults, while:
  - excluding server-managed `readOnly` fields (e.g. HATEOAS `_links`,
    `createdBy`) so response-only fields are never advertised as writable;
  - resolving a property's `$ref` to report its real type (e.g. an enum string
    renders as `string`, not `object`);
  - expanding each component schema at most once, so diamond / duplicate-`$ref`
    `allOf` graphs cannot cause exponential blow-up during spec load;
  - rendering array-rooted bodies as `body: array` (passed under a lone `body`
    key) instead of the contradictory object/top-level phrasing;
  - leaving a genuine lone field named `body` untouched (the dispatcher unwrap
    is schema-aware), and degrading to "no fields" on any malformed schema
    rather than crashing the whole load.

## [0.2.1] — 2026-06-11

### Changed
- Tightened the `fastmcp` floor to `>=3.0` to match what the project is tested
  and shipped against, preventing a silent resolve back to 2.x behaviour (#6).

### Tests
- Tool registration is now exercised against a **real** `FastMCP` instance
  instead of a mocked `mcp.tool`, and a regression test asserts each registered
  tool's input schema exposes only `{action, params}`. This locks in the
  minimal-handler-signature contract so a future leak of an unserialisable type
  (e.g. a `Dispatcher` default arg) fails the suite instead of crashing at
  startup under fastmcp 3.x. Defensive follow-up to catalyst-sdwan #52/#53; no
  runtime behaviour change (#6).

[0.2.1]: https://github.com/thomaschristory/thousand-eyes-super-mcp/releases/tag/v0.2.1

## [0.2.0] — 2026-06-11

### Fixed
- **Server no longer crashes with `FileNotFoundError: Config file not found`
  when installed via `uv tool`/pipx and launched by an MCP client** (#3). The
  YAML config file is now **optional**: the bearer token (and other settings)
  can be supplied entirely through environment variables or a `.env` file.
- `.env` discovery now searches the **current working directory** (and next to
  `--config`) instead of upward from the installed package's `site-packages`
  directory, so a `.env` in your project dir is actually found. Exported shell
  variables still take precedence over `.env` values.

### Added
- Env-first configuration on `pydantic-settings`. Precedence, highest first:
  **CLI overrides > environment variables > YAML file > built-in defaults**.
  Documented env vars: `THOUSANDEYES_BEARER_TOKEN`,
  `THOUSANDEYES_ACCOUNT_GROUP_ID`, `THOUSANDEYES_VERIFY_SSL`.
- Fail-fast credential check: a missing bearer token now raises a clear,
  actionable error **before** the (expensive) spec load/auto-fetch runs.
- `load_config(path, *, required=False)` — pass `required=True` (done
  automatically when `--config` is given explicitly) to error on a missing
  file the user asked for; otherwise a missing file falls back to env+defaults.

### Changed
- New runtime dependencies: `pydantic>=2.0`, `pydantic-settings>=2.0`.

[0.2.0]: https://github.com/thomaschristory/thousand-eyes-super-mcp/releases/tag/v0.2.0

## [0.1.0] — 2026-05-27

First release. The `thousand-eyes-mcp` CLI runs end-to-end against the
ThousandEyes v7 API and exposes the full surface as MCP tools.

### Added
- FastMCP server that dynamically registers MCP tools from the ThousandEyes
  OpenAPI spec at startup.
- Bundled OpenAPI spec for ThousandEyes **7.0.88** (current public unified spec).
- Adaptive size-driven tool splitter: section → sub-tag → URL path depth
  (3/4/5), with `<4`-op buckets collapsed into `<parent>_misc`. Default cap
  80 actions per tool, configurable via `thousand_eyes_mcp.max_actions_per_tool`.
- Action names derived from `(method, path, tag)` — stable across upstream
  `operationId` churn between releases. The upstream `operationId` is
  preserved on `OperationSpec` as a back-reference for the `--diff` utility.
- Upstream authentication: long-lived OAuth2 bearer token from account
  settings, set verbatim as `Authorization: Bearer <token>`. No login flow.
- Pagination: cursor (primary; follows `_links.next.href`) and offset/limit.
  Auto-follow up to N pages; responses wrap with
  `_paginated: {pages, truncated, next_cursor}` metadata when stitched.
- Reserved per-call parameters: `_max_pages`, `_page_size`, `_auto_follow` —
  stripped before the HTTP request.
- Configurable retry on transient HTTP failures (429/502/503/504 by
  default); never retries mutating methods unless `retry_mutating: true`.
  Exponential backoff with jitter, capped.
- Optional default `aid` (account group ID) injected automatically into
  every request that accepts it.
- Transports: **stdio** (default), **SSE**, **streamable-http**.
- Bearer-token auth middleware for HTTP transports, with bind-safety logic
  that demotes `0.0.0.0` to `127.0.0.1` when `transport.auth.type=none`
  unless `--insecure-allow-public` is passed.
- `--diff <v1> <v2>` CLI utility: compares two on-disk specs and prints
  added/removed/changed operations (with per-parameter drift).
- `fetch`, `list-versions`, `discover-versions` standalone subcommands.
- Read-only by default. `--read-write` opt-in to register
  `POST`/`PUT`/`DELETE`/`PATCH` endpoints.
- Docker / docker-compose, GitHub Actions for lint / test / docker / docs /
  release / dependabot auto-merge / milestone rollover.

[0.1.0]: https://github.com/thomaschristory/thousand-eyes-super-mcp/releases/tag/v0.1.0
