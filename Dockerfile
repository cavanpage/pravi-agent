# -----------------------------------------------------------------------------
# pravi runtime image — used by pravi-web + both worker services in docker-compose.
#
# Multi-stage build:
#   1. `web-build`  compiles the React app once (React needs node; we don't
#                   want node in the final image bigger than necessary).
#   2. `runtime`    is the actual serving image: Python 3.12 + uv + git + node.
#
# Why node in the runtime image too? The dev agent runs `npm test` / `npm run
# build` etc. INSIDE per-ticket git worktrees for target repos that happen to
# be JS/TS. Without node in the runtime image those commands would fail. If
# your target repos are all Python this could be trimmed.
#
# Why non-root? The Claude CLI refuses `--dangerously-skip-permissions` when
# it detects root (safety guard) — pravi's SDK passes that flag by design
# (permission_mode="bypassPermissions"). So the runtime user is `pravi`
# (UID 1000). Bind-mounted paths land under /home/pravi in compose.
# -----------------------------------------------------------------------------

# ---- Stage 1: build web/dist ----
FROM node:20-alpine AS web-build
WORKDIR /web
# Copy manifest first so npm install can be cached independently of the code.
COPY web/package.json web/package-lock.json ./
RUN npm ci --silent
COPY web/ .
RUN npm run build --silent


# ---- Stage 2: runtime ----
FROM python:3.12-slim AS runtime

# System deps:
#   - git   → worktree operations (git worktree add, git push)
#   - curl  → uv installer download
#   - ca-certificates → HTTPS to Anthropic / GitHub / Cloudflare APIs
#   - nodejs / npm → per-target-repo `npm test`, `npm run build` in worktrees
# `--no-install-recommends` keeps the image lean; apt lists cleaned in the same
# layer so they don't bloat the final size.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# claude-agent-sdk is a Python wrapper — the actual work runs in the
# `claude` CLI subprocess (distributed as an npm package). Without this
# binary the SDK errors out on first call with ProcessError. Installed
# globally as root before the USER switch below so `claude` is on PATH
# for every user.
RUN npm install -g @anthropic-ai/claude-code

# ---- Non-root user ----
# UID 1000 matches the typical first host user, so bind-mounted files
# from macOS (Docker Desktop translates ownership) or Linux (matched UID)
# read/write cleanly without permission dances.
RUN useradd --uid 1000 --create-home --shell /bin/bash pravi \
    && mkdir -p /app \
    && chown pravi:pravi /app

# Everything after this line runs as `pravi`, including uv install +
# `uv sync` so the virtualenv is owned by the runtime user.
USER pravi
ENV HOME=/home/pravi \
    PATH=/home/pravi/.local/bin:/usr/local/bin:/usr/bin:/bin

# Install uv into pravi's home. Version-agnostic — uv self-updates on
# first use if the lockfile requires it.
ADD --chown=pravi:pravi https://astral.sh/uv/install.sh /tmp/uv-install.sh
RUN sh /tmp/uv-install.sh && rm /tmp/uv-install.sh

WORKDIR /app

# Install Python deps first (cached until pyproject/uv.lock change).
COPY --chown=pravi:pravi pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Now the source + everything Hatch needs to build the wheel.
COPY --chown=pravi:pravi src/ ./src/
COPY --chown=pravi:pravi alembic.ini ./
COPY --chown=pravi:pravi scripts/ ./scripts/
# README.md is referenced by pyproject.toml (`readme = "README.md"`), which
# Hatch reads at wheel-build time — omit it and the second `uv sync` fails
# with "readme file not found".
COPY --chown=pravi:pravi README.md ./
RUN uv sync --frozen

# Built web assets from stage 1 land where the FastAPI app expects them.
COPY --from=web-build --chown=pravi:pravi /web/dist ./web/dist

# Sensible defaults; overridden by docker-compose for each service.
ENV PYTHONUNBUFFERED=1 \
    PRAVI_LOG_LEVEL=INFO

EXPOSE 8765

# Default command is the web server; docker-compose overrides `command:` per
# service (workers use `python -m pravi.worker ...`).
CMD ["uv", "run", "pravi", "web", "--host", "0.0.0.0", "--port", "8765"]
