# Architecture overview

```
                ┌────────────────────────────────────────────────┐
                │              FastMCP client (Claude, …)        │
                └──────────────────────┬─────────────────────────┘
                                       │ MCP (stdio | SSE | streamable-http)
                                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                       thousand-eyes-mcp process                          │
│                                                                          │
│   ┌──────────────┐    ┌──────────────┐    ┌─────────────────────────┐    │
│   │  config.py   │──▶ │   loader.py  │──▶ │       SpecIndex         │    │
│   │ (YAML+envs)  │    │  (OpenAPI →  │    │  action_name → OpSpec   │    │
│   │              │    │   ToolGroup) │    │  ToolGroup list         │    │
│   └──────┬───────┘    └──────────────┘    └────────────┬────────────┘    │
│          │                                              │                │
│          ▼                                              ▼                │
│   ┌──────────────┐    ┌──────────────┐    ┌─────────────────────────┐    │
│   │  auth.py     │◀───│ dispatcher.py│◀───│        tools.py         │    │
│   │ (Bearer)     │    │  (httpx +    │    │ (one tool per group;    │    │
│   │              │    │   retry +    │    │  action+params handler) │    │
│   │              │    │   pagination)│    │                         │    │
│   └──────────────┘    └──────┬───────┘    └─────────────────────────┘    │
│                              │                                           │
│                              ▼                                           │
│                  https://api.thousandeyes.com/v7                         │
└──────────────────────────────────────────────────────────────────────────┘
```

## Module roles

| Module | Responsibility |
|---|---|
| `config.py` | Load `thousand-eyes-mcp.yaml`, interpolate `${ENV_VAR}`, validate bearer auth requirements. |
| `loader.py` | Parse the OpenAPI spec, derive stable action names, run the adaptive splitter, build the flat `SpecIndex`. |
| `auth.py` | Hold the bearer token and produce the `Authorization` header. No login flow — ThousandEyes tokens are long-lived. |
| `dispatcher.py` | Async HTTP client with path-param substitution, retry, optional default `aid` injection, and pagination orchestration. |
| `pagination.py` | Cursor (`_links.next.href`) and offset/limit paginators. |
| `tools.py` | Register one MCP tool per `ToolGroup`. Each tool's description lists every action and its parameter shape. |
| `transport_auth.py` | Bearer-token middleware for HTTP transports + bind-safety logic. |
| `diff.py` | Compare two on-disk spec versions and report added/removed/changed operations. |
| `fetcher/` | Download specs from DevNet pubhub and discover new versions. |
| `cli/` | `fetch`, `list-versions`, `discover-versions` standalone subcommands. |
