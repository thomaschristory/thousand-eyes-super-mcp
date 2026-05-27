# Changelog

All notable changes to thousand-eyes-super-mcp will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
