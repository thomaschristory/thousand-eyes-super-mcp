# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

# uv for fast, reproducible installs — pinned to an immutable version + digest
# (mirrors the full-SHA discipline applied to GitHub Actions). Build-time only;
# not present in the runtime image.
COPY --from=ghcr.io/astral-sh/uv:0.11.21@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock* README.md ./
COPY thousand_eyes_mcp ./thousand_eyes_mcp

# Install into /app/.venv
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# --- runtime ---
FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /app /app
COPY thousand-eyes-mcp.yaml ./

ENV PATH="/app/.venv/bin:$PATH"

# Drop privileges: run as a non-root user (least privilege / smaller blast
# radius for the network-reachable SSE transport). `app` owns /app from the
# build. Note: a specs dir bind-mounted at runtime keeps its host ownership, so
# for the auto_fetch (version-bump) path the mounted dir must be writable by
# uid 10001 — otherwise mount a pre-populated specs dir (read-only is fine).
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

# Specs are mounted at runtime — not baked into the image
# -----------------------------------------------------------------------
# Usage:
#
# Build:
#   docker build -t thousand-eyes-super-mcp .
#
# Claude Desktop (stdio):
#   docker run -i --rm \
#     -e THOUSANDEYES_BEARER_TOKEN=... \
#     -v $(pwd)/specs:/app/specs \
#     thousand-eyes-super-mcp
#
# Network (SSE):
#   docker run -p 8000:8000 \
#     -e THOUSANDEYES_BEARER_TOKEN=... \
#     -v $(pwd)/specs:/app/specs \
#     thousand-eyes-super-mcp --transport sse --host 0.0.0.0 --port 8000
# -----------------------------------------------------------------------

ENTRYPOINT ["thousand-eyes-mcp"]
