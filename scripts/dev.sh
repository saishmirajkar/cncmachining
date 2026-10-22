#!/usr/bin/env bash
# Start the local development stack: PostgreSQL + Redis (system services), API, RQ worker and Vite.
# Logs go to backend/var/logs. Stop with: scripts/dev.sh stop
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/backend/var/logs"
PIDS="$ROOT/backend/var/pids"
mkdir -p "$LOGS" "$PIDS"

if [[ "${1:-}" == "stop" ]]; then
  for f in "$PIDS"/*.pid; do [[ -f "$f" ]] && kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; done
  echo "stopped"; exit 0
fi

pg_ctlcluster 16 main start 2>/dev/null || true
redis-cli ping >/dev/null 2>&1 || redis-server --daemonize yes >/dev/null

cd "$ROOT/backend"
.venv/bin/alembic upgrade head
.venv/bin/python -m app.seed.seed
export CNC_QUEUE_BACKEND=rq
nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 >"$LOGS/api.log" 2>&1 & echo $! >"$PIDS/api.pid"
nohup .venv/bin/python -m app.worker.run >"$LOGS/worker.log" 2>&1 & echo $! >"$PIDS/worker.pid"
cd "$ROOT/frontend"
nohup npx vite --host 127.0.0.1 --port 5173 >"$LOGS/web.log" 2>&1 & echo $! >"$PIDS/web.pid"
echo "API http://127.0.0.1:8000/api/health  ·  UI http://127.0.0.1:5173"
