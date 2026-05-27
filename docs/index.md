# thousand-eyes-super-mcp

FastMCP server for the Cisco ThousandEyes API, generated dynamically from
the official OpenAPI spec.

ThousandEyes is Cisco's SaaS for end-to-end network and application
visibility — synthetic agent tests, BGP monitoring, internet weather,
endpoint experience. This server exposes the entire v7 REST API as MCP
tools, ready to drop into Claude Desktop, Claude Code, Cursor, or any
MCP-aware client.

## Quick start

```bash
uv tool install thousand-eyes-super-mcp
echo "THOUSANDEYES_BEARER_TOKEN=your-token" > .env
thousand-eyes-mcp
```

## Highlights

- **Bearer token auth.** Long-lived OAuth2 tokens from account settings.
- **Adaptive splitter.** Sections → sub-tags → URL path depth; bundles small
  siblings into `<parent>_misc`.
- **Stable action names.** Derived from `(method, path, tag)` instead of
  upstream `operationId`, which can churn between releases.
- **Cursor + offset pagination.** Auto-follows `_links.next.href` and
  offset/limit; override per call with `_max_pages`, `_page_size`,
  `_auto_follow`.
- **Multiple transports.** stdio, SSE, streamable-http.

See the [CLI reference](reference/cli.md) for the full command surface.
