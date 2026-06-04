#!/usr/bin/env bash
# One-shot local dev launcher.
#
#   ./scripts/dev.sh           # default: clean + build + run (foreground; Ctrl-C tears down)
#   ./scripts/dev.sh --no-build  skip the React build (faster restart)
#   ./scripts/dev.sh --no-vite   don't also start the Vite dev server
#   ./scripts/dev.sh --vite      do start the Vite dev server (default: no)
#   ./scripts/dev.sh --reset-db  drop + recreate the docker volumes (DANGER)
#
# What it does:
#   1. Stops any lingering pravi processes from a previous run.
#   2. Ensures docker compose stack (Postgres + Temporal + UI) is up.
#   3. Applies alembic migrations (idempotent).
#   4. Registers Temporal search attributes (idempotent).
#   5. Builds the React frontend into web/dist (unless --no-build).
#   6. Starts: features worker, llm worker, pravi web (background).
#   7. Optionally starts Vite dev server on :5173 (with --vite).
#   8. Tails combined logs to stdout. Ctrl-C terminates everything.

set -euo pipefail

ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$ROOT"

LOG_DIR="$ROOT/.pravi/logs"
mkdir -p "$LOG_DIR"

DO_BUILD=1
DO_VITE=0
RESET_DB=0
for arg in "$@"; do
  case "$arg" in
    --no-build) DO_BUILD=0 ;;
    --no-vite)  DO_VITE=0  ;;
    --vite)     DO_VITE=1  ;;
    --reset-db) RESET_DB=1 ;;
    --help|-h)
      sed -n '2,17p' "$0"
      exit 0
      ;;
    *) echo "unknown flag: $arg (use --help)"; exit 2 ;;
  esac
done

c_blue=$'\033[1;34m'; c_grn=$'\033[1;32m'; c_ylw=$'\033[1;33m'; c_red=$'\033[1;31m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
step()  { echo "${c_blue}▶${c_off} $*"; }
ok()    { echo "${c_grn}✓${c_off} $*"; }
warn()  { echo "${c_ylw}!${c_off} $*"; }
fail()  { echo "${c_red}✗${c_off} $*" >&2; }

# -------- 1. Stop any existing pravi processes --------
step "stopping stale pravi processes"
# Patterns cover:
#   - python -m pravi.worker ...        (worker subprocesses)
#   - uv run pravi web ... + the bin/pravi child it spawns
#   - bin/pravi web                     (the venv-script direct invocation
#                                        — what `uv run` leaves behind on
#                                        the listening side)
#   - vite (frontend dev server)
# Ignore "no such process".
pkill -f "pravi.worker"   >/dev/null 2>&1 || true
pkill -f "pravi web"      >/dev/null 2>&1 || true
pkill -f "vite"           >/dev/null 2>&1 || true
# Belt + braces: anything still holding :8765 (e.g. orphan uvicorn) goes.
if command -v lsof >/dev/null 2>&1; then
  port_pid="$(lsof -ti :8765 2>/dev/null || true)"
  if [[ -n "$port_pid" ]]; then
    kill "$port_pid" 2>/dev/null || true
  fi
fi
sleep 1
ok "cleared"

# -------- 2. Docker compose --------
if [[ "$RESET_DB" -eq 1 ]]; then
  warn "--reset-db: removing volumes (DB + Temporal state will be lost)"
  docker compose down -v >/dev/null
fi
step "starting docker stack (Postgres + Temporal + UI)"
docker compose up -d >/dev/null
# Wait for Temporal to accept calls — it's the slowest to come up.
echo -n "  waiting for Temporal "
until docker exec pravi-temporal temporal --address temporal:7233 \
       operator namespace describe default >/dev/null 2>&1; do
  echo -n "."
  sleep 1
done
echo
ok "stack up"

# -------- 3. Alembic migrations --------
step "applying alembic migrations"
uv run alembic upgrade head >/dev/null
ok "schema up to date"

# -------- 4. Temporal search attributes --------
step "registering Temporal search attributes"
./scripts/setup-temporal.sh >/dev/null
ok "attributes registered"

# -------- 5. Frontend build --------
if [[ "$DO_BUILD" -eq 1 ]]; then
  step "building React app"
  if [[ ! -d web/node_modules ]]; then
    (cd web && npm install --silent) >/dev/null
  fi
  (cd web && npm run build --silent) >/dev/null
  ok "web/dist built"
else
  warn "skipping React build (--no-build); FastAPI will serve whatever's in web/dist/"
fi

# -------- 6. Start workers + API in background --------
PIDS=()
start_bg() {
  local name="$1"; shift
  local logfile="$LOG_DIR/$name.log"
  step "starting $name → $logfile"
  ( "$@" >"$logfile" 2>&1 ) &
  PIDS+=($!)
  ok "$name pid=$!"
}

start_bg features-worker uv run python -m pravi.worker --queue features
start_bg llm-worker      uv run python -m pravi.worker --queue llm --max-activities 2
start_bg pravi-web       uv run pravi web --port 8765

if [[ "$DO_VITE" -eq 1 ]]; then
  start_bg vite           bash -c "cd web && npm run dev"
fi

# -------- 7. Tail logs + trap Ctrl-C --------
cleanup() {
  echo
  step "shutting down"
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  # Give them a moment to exit cleanly.
  sleep 1
  for pid in "${PIDS[@]}"; do
    kill -9 "$pid" 2>/dev/null || true
  done
  ok "all processes stopped"
  exit 0
}
trap cleanup INT TERM

echo
echo "${c_dim}─────────────────────────────────────────────────────────────${c_off}"
echo "  web UI:     ${c_grn}http://localhost:8765${c_off}"
echo "  Temporal:   ${c_grn}http://localhost:8233${c_off}"
if [[ "$DO_VITE" -eq 1 ]]; then
  echo "  Vite (dev): ${c_grn}http://localhost:5173${c_off}"
fi
echo "  logs:       ${c_dim}$LOG_DIR/*.log${c_off}"
echo "  Ctrl-C to stop everything"
echo "${c_dim}─────────────────────────────────────────────────────────────${c_off}"
echo

# Tail all logs with a prefix per file so you can tell them apart.
# `tail -F` survives log rotation; --pid stops when the parent dies.
tail -n 0 -F "$LOG_DIR"/*.log 2>/dev/null | sed -u \
  -e "s#^==> $LOG_DIR/features-worker.log <==#${c_blue}[features]${c_off}#" \
  -e "s#^==> $LOG_DIR/llm-worker.log <==#${c_blue}[llm]${c_off}#" \
  -e "s#^==> $LOG_DIR/pravi-web.log <==#${c_blue}[web]${c_off}#" \
  -e "s#^==> $LOG_DIR/vite.log <==#${c_blue}[vite]${c_off}#" \
  &
TAIL_PID=$!

# Poll: if any background process exits, tear the rest down.
# (Portable substitute for `wait -n`, which needs bash >= 4.3 — macOS bash is 3.2.)
while :; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      warn "process $pid exited unexpectedly; tearing down"
      cleanup
    fi
  done
  sleep 2
done
