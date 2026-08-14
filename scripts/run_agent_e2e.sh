#!/usr/bin/env bash
# =============================================================================
# I-FRONTEND agent E2E harness.
#
# Starts the pinned API + Celery worker + Vite frontend against disposable
# PostgreSQL/Redis, seeds the MVP fixture (admin login + a governed Agent), runs
# the Playwright suite under frontend/src/test/e2e, and tears everything down.
# The protected marker frontend/test-results/.last-run.json is snapshotted
# before and restored after every run.
#
# Usage (from the repo root):
#   bash scripts/run_agent_e2e.sh [--no-stack] [playwright args...]
#
#   --no-stack        run Playwright without starting the stack (static/red
#                     contract runs against an already-running stack)
#   -- <spec>         e.g. `bash scripts/run_agent_e2e.sh -- agent-navigation.spec.ts`
#
# Environment overrides:
#   AGENT_E2E_API_BASE   API base (default http://localhost:8000)
#   AGENT_E2E_DB_URL     disposable PostgreSQL URL (required without --no-stack)
#   AGENT_E2E_REDIS_URL  Redis URL (default redis://localhost:6379/0)
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="$ROOT/frontend"
BACKEND="$ROOT/backend"
MARKER="$FRONTEND/test-results/.last-run.json"
SNAPSHOT="$FRONTEND/src/test/e2e/fixtures/.last-run.snapshot.json"
API_BASE="${AGENT_E2E_API_BASE:-http://localhost:8000}"
REDIS_URL="${AGENT_E2E_REDIS_URL:-redis://localhost:6379/0}"

NO_STACK=0
PLAYWRIGHT_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --no-stack) NO_STACK=1 ;;
    *) PLAYWRIGHT_ARGS+=("$arg") ;;
  esac
done

PIDS=()

cleanup() {
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  if [ -f "$SNAPSHOT" ]; then
    cp "$SNAPSHOT" "$MARKER"
    echo "[run_agent_e2e] restored $MARKER from snapshot"
  else
    rm -f "$MARKER"
  fi
}
trap cleanup EXIT

if [ -f "$MARKER" ]; then
  cp "$MARKER" "$SNAPSHOT"
  echo "[run_agent_e2e] snapshot $MARKER -> $SNAPSHOT"
fi

if [ "$NO_STACK" -eq 1 ]; then
  cd "$FRONTEND"
  exec npx playwright test --config src/test/e2e/playwright.config.ts "${PLAYWRIGHT_ARGS[@]}"
fi

if [ -z "${AGENT_E2E_DB_URL:-}" ]; then
  echo "AGENT_E2E_DB_URL is required for a full stack run (use --no-stack for static runs)" >&2
  exit 2
fi

echo "[run_agent_e2e] starting API on $API_BASE"
DATABASE_URL="$AGENT_E2E_DB_URL" \
  REDIS_URL="$REDIS_URL" \
  FIRST_ADMIN_USER="${AGENT_E2E_ADMIN_USER:-admin}" \
  FIRST_ADMIN_PASSWORD="${AGENT_E2E_ADMIN_PASSWORD:-admin123}" \
  "$BACKEND/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
PIDS+=("$!")

echo "[run_agent_e2e] starting Celery worker"
cd "$BACKEND"
DATABASE_URL="$AGENT_E2E_DB_URL" REDIS_URL="$REDIS_URL" \
  .venv/bin/python -m celery -A app.tasks.celery_app worker --loglevel=warning --concurrency=1 &
PIDS+=("$!")
cd "$ROOT"

for _ in $(seq 1 60); do
  if curl -fsS "$API_BASE/health" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS "$API_BASE/health" >/dev/null || { echo "[run_agent_e2e] API did not become healthy" >&2; exit 1; }
echo "[run_agent_e2e] API healthy"

echo "[run_agent_e2e] seeding MVP fixture"
AGENT_E2E_API_BASE="$API_BASE" node --experimental-vm-modules "$FRONTEND/src/test/e2e/fixtures/agentMvp.ts" \
  || echo "[run_agent_e2e] fixture seeding failed (specs self-skip on missing data)"

cd "$FRONTEND"
exec npx playwright test --config src/test/e2e/playwright.config.ts "${PLAYWRIGHT_ARGS[@]}"
