# Data flow

## Startup

1. `main()` parses CLI args. Subcommand tokens (`fetch`, `list-versions`,
   `discover-versions`) short-circuit before the server starts.
2. `load_config()` reads `thousand-eyes-mcp.yaml`, interpolates `${ENV_VAR}`
   references, and validates bearer-auth requirements (length, type).
3. `_maybe_auto_fetch()` downloads the active spec from DevNet pubhub if
   `auto_fetch: true` and the version directory is empty.
4. `SpecLoader.load()` merges every spec file under `specs/<active_version>/`,
   extracts operations, runs the adaptive splitter, and returns a
   `SpecIndex` keyed by `action_name`.
5. `Dispatcher.connect()` verifies the bearer token is set (no HTTP call).
6. `register_tools()` registers one MCP tool per `ToolGroup`. Each tool's
   description lists every action with its parameter shape.
7. `mcp.run(...)` enters the transport loop (stdio / SSE / streamable-http).

## Request

1. MCP client calls a tool with `(action, params)`.
2. The tool handler looks up the action in its `valid_actions` set and
   delegates to `dispatcher.call(action, params)`.
3. `Dispatcher._execute_with_retry()` strips reserved params
   (`_max_pages`, `_page_size`, `_auto_follow`) and picks a paginator if
   the op has detectable pagination.
4. For paginated calls, `Paginator.paginate()` drives `_execute_one_with_retry`
   in a loop, following `_links.next.href` (cursor) or incrementing
   `offset` (offset). The combined pages are returned under a stitched
   list key with a trailing `_paginated` envelope.
5. `_execute()` substitutes path params, routes the rest to query / body,
   injects the default `aid` if configured, attaches the `Authorization`
   header, and calls `_send_with_retry()`.
6. `_send_with_retry()` retries on configured statuses (default
   `429/502/503/504`) with exponential backoff + jitter, capped.
7. The response body is JSON-decoded and returned to the tool handler.
