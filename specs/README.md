# Specs

OpenAPI specs for ThousandEyes, organised by version.

Place each spec file (yaml/yml/json) under `specs/<version>/`. The loader
glob-merges every file in the directory at startup, so split-spec releases
work without code change.

The current bundled version is **7.0.88** (the unified v7 OAS published by
Cisco on DevNet pubhub). To add a new version:

1. Append the download URL to `KNOWN_SPEC_URLS` in
   `thousand_eyes_mcp/fetcher/__init__.py`.
2. Run `thousand-eyes-mcp fetch <new-version>` to cache it locally, or
   start the server with `auto_fetch: true` and the new
   `active_version` and it will be downloaded on first launch.
