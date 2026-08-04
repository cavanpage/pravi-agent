#!/usr/bin/env bash
# Copy the host's Claude Code credential into the container-visible auth dir
# so the dockerized pravi-worker-llm inherits your existing `claude login`
# session — no interactive login inside the container required.
#
# Where credentials live per platform:
#   macOS  — Keychain, service "Claude Code-credentials"
#   Linux  — ~/.claude/.credentials.json (plaintext JSON)
#
# The container reads from /root/.claude/.credentials.json, which we bind
# to ~/.pravi/claude-auth on the host in docker-compose.yml.
#
# Idempotent: safe to re-run. Re-run when your session expires (usually
# months) or after a fresh `claude login` on the host.
#
# Usage:
#   ./scripts/bootstrap-claude-auth.sh
#
set -euo pipefail

DEST_DIR="${HOME}/.pravi/claude-auth"
DEST_FILE="${DEST_DIR}/.credentials.json"

mkdir -p "$DEST_DIR"

case "$(uname -s)" in
  Darwin)
    if ! security find-generic-password \
           -s "Claude Code-credentials" \
           -a "$USER" \
           -w > "$DEST_FILE" 2>/dev/null; then
      echo "error: no Claude credential found in macOS Keychain." >&2
      echo "  run 'claude login' on the host first, then re-run this." >&2
      rm -f "$DEST_FILE"
      exit 1
    fi
    ;;
  Linux)
    SRC="${HOME}/.claude/.credentials.json"
    if [[ ! -f "$SRC" ]]; then
      echo "error: $SRC not found." >&2
      echo "  run 'claude login' on the host first, then re-run this." >&2
      exit 1
    fi
    cp "$SRC" "$DEST_FILE"
    ;;
  *)
    echo "error: unsupported OS '$(uname -s)' — only macOS + Linux handled." >&2
    exit 2
    ;;
esac

chmod 600 "$DEST_FILE"

echo "wrote $DEST_FILE ($(wc -c < "$DEST_FILE" | tr -d ' ') bytes)"
echo "container CLI will read this on next start. verify with:"
echo "  docker exec pravi-worker-llm sh -c 'echo hi | claude --print'"
