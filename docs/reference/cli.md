# CLI reference

The `thousand-eyes-mcp` entry point launches the FastMCP server by default,
and also exposes a small set of standalone subcommands that exit without
starting the server.

## Bare invocation (server)

```bash
thousand-eyes-mcp [--config PATH] [--transport stdio|sse|streamable-http]
                  [--host HOST] [--port PORT] [--read-write]
                  [--version VERSION] [--max-actions-per-tool N]
                  [--insecure-allow-public]
                  [--diff OLD NEW]
                  [--show-version]
```

| Flag | Purpose |
|---|---|
| `--config PATH` | Path to the config file. Defaults to `./thousand-eyes-mcp.yaml`. |
| `--transport` | Override `transport.mode`. |
| `--host`, `--port` | Override `transport.host` / `transport.port`. |
| `--read-write` | Register POST/PUT/DELETE/PATCH endpoints. Read-only by default. |
| `--version` | Override `thousand_eyes_mcp.active_version`. |
| `--max-actions-per-tool N` | Override the adaptive-splitter cap (0 disables splitting). |
| `--insecure-allow-public` | Permit binding `0.0.0.0` with `transport.auth.type=none`. Not recommended. |
| `--diff OLD NEW` | Diff two on-disk spec versions and exit. |
| `--show-version` | Print version and exit. |

## Subcommands

The first positional token is matched against the subcommand set
(`fetch`, `list-versions`, `discover-versions`) **before** the main argparse
parser runs. When a subcommand matches, the server is not started. All
non-data output routes to stderr so stdio-mode JSON-RPC is never polluted.

### `fetch`

Download an OpenAPI spec for one or all known ThousandEyes versions
without starting the server.

```bash
thousand-eyes-mcp fetch <version> [--config PATH] [--specs-dir DIR]
thousand-eyes-mcp fetch --all-known [--config PATH] [--specs-dir DIR]
```

Positional `<version>` and `--all-known` are mutually exclusive and one is
required. The spec lands under `{specs_dir}/{version}/{filename}`. TLS
verification is always on, regardless of `thousand_eyes.verify_ssl` — the
spec source is a public CDN.

Example:

```bash
thousand-eyes-mcp fetch 7.0.88
thousand-eyes-mcp fetch --all-known --specs-dir ./specs
```

### `list-versions`

Enumerate every version baked into this build's `KNOWN_SPEC_URLS` and any
extra version directories already present on disk. **Offline** — makes no
network calls.

```bash
thousand-eyes-mcp list-versions [--config PATH] [--specs-dir DIR]
```

Example:

```bash
$ thousand-eyes-mcp list-versions
Known versions (hardcoded in KNOWN_SPEC_URLS):
  7.0.88

Versions on disk under ./specs/:
  7.0.88  (cached)
```

### `discover-versions` *(experimental)*

Scrape Cisco DevNet's docs landing page
(`https://developer.cisco.com/docs/thousandeyes/`) for ThousandEyes spec
versions and print a diff vs the hardcoded `KNOWN_SPEC_URLS` table. Helper
only — it never mutates the hardcoded table; the maintainer copies new
entries in by hand after reviewing.

```bash
thousand-eyes-mcp discover-versions
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Every hardcoded entry was also discovered on DevNet. |
| `1` | One or more hardcoded entries are no longer visible on DevNet. |
| `2` | `DiscoveryError` or `httpx.HTTPError`. |
