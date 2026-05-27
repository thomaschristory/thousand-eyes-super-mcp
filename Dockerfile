# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

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
