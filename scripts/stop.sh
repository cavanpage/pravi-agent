#!/usr/bin/env bash
# Stop all pravi local processes. Doesn't touch docker — use
# `docker compose stop` for that.
set -euo pipefail

killed=0
# `pravi web` covers both `uv run pravi web` AND the venv `bin/pravi web`
# child it spawns (which is what actually binds :8765).
for pat in "pravi.worker" "pravi web" "vite"; do
  if pkill -f "$pat" 2>/dev/null; then
    echo "stopped: $pat"
    killed=$((killed + 1))
  fi
done

# Belt + braces: anything still holding :8765 (orphan uvicorn etc) goes.
if command -v lsof >/dev/null 2>&1; then
  port_pid="$(lsof -ti :8765 2>/dev/null || true)"
  if [[ -n "$port_pid" ]]; then
    kill "$port_pid" 2>/dev/null || true
    echo "stopped: pid $port_pid (was bound to :8765)"
    killed=$((killed + 1))
  fi
fi

if [[ "$killed" -eq 0 ]]; then
  echo "nothing to stop"
fi
