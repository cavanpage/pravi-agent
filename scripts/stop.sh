#!/usr/bin/env bash
# Stop all pravi local processes. Doesn't touch docker — use
# `docker compose stop` for that.
set -euo pipefail

killed=0
for pat in "pravi.worker" "pravi.api.app:app" "uv run pravi web" "vite"; do
  if pkill -f "$pat" 2>/dev/null; then
    echo "stopped: $pat"
    killed=$((killed + 1))
  fi
done

if [[ "$killed" -eq 0 ]]; then
  echo "nothing to stop"
fi
