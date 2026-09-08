#!/usr/bin/env bash
# =============================================================================
# M1 stabilization gate: a fresh `docker compose -f docker-compose.v2.yml up
# --build` must reach healthy frontend/backend services against an empty
# database. Runs standalone (locally or in CI); never touches a real
# developer's .env — if one exists, it's backed up and restored exactly,
# regardless of outcome.
#
# Usage (from the repo root):
#   bash scripts/verify_m1_stabilization_gate.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SMOKE_PROJECT="ontexus-m1-smoke-$$"
SMOKE_VOLUME_FILTER="label=com.docker.compose.project=$SMOKE_PROJECT"
ENV_BACKUP=""

cleanup() {
  local status=$?
  echo "[m1-gate] cleaning up (exit code so far: $status)"
  docker compose -p "$SMOKE_PROJECT" -f docker-compose.v2.yml down -v --remove-orphans >/dev/null 2>&1 || true
  if [ -n "$ENV_BACKUP" ]; then
    mv -f "$ENV_BACKUP" .env
    echo "[m1-gate] restored original .env"
  elif [ -f .env ]; then
    rm -f .env
    echo "[m1-gate] removed the .env this run created (none existed before)"
  fi
  exit "$status"
}
trap cleanup EXIT

# .env is real developer config (git-ignored) and, for local non-Docker dev,
# legitimately points DATABASE_URL/REDIS_URL/etc. at localhost — wrong for
# Docker Compose's service-name networking. Use .env.example's Docker-correct
# defaults for this run only, and always restore whatever was there before.
if [ -f .env ]; then
  ENV_BACKUP="$(mktemp)"
  cp .env "$ENV_BACKUP"
  echo "[m1-gate] backed up existing .env"
fi
cp .env.example .env

echo "[m1-gate] tearing down any stale state for $SMOKE_PROJECT"
docker compose -p "$SMOKE_PROJECT" -f docker-compose.v2.yml down -v --remove-orphans >/dev/null 2>&1 || true

echo "[m1-gate] docker compose up --build -d --wait"
docker compose -p "$SMOKE_PROJECT" -f docker-compose.v2.yml up --build -d --wait

MIGRATION_ID="$(docker compose -p "$SMOKE_PROJECT" -f docker-compose.v2.yml ps -a -q migration)"
test -n "$MIGRATION_ID"
MIGRATION_STATUS="$(docker inspect -f '{{.State.Status}}' "$MIGRATION_ID")"
MIGRATION_EXIT="$(docker inspect -f '{{.State.ExitCode}}' "$MIGRATION_ID")"
echo "[m1-gate] migration container: status=$MIGRATION_STATUS exit=$MIGRATION_EXIT"
test "$MIGRATION_STATUS" = "exited"
test "$MIGRATION_EXIT" = "0"

test -n "$(docker volume ls -q --filter "$SMOKE_VOLUME_FILTER")"

docker compose -p "$SMOKE_PROJECT" -f docker-compose.v2.yml ps --status running backend frontend

echo "[m1-gate] checking backend health"
# backend/frontend have no Compose healthcheck: (only db/redis/neo4j/minio/
# chromadb do), so `--wait` only guarantees the container started, not that
# uvicorn inside has finished booting — poll instead of a single shot.
HEALTH=""
for i in $(seq 1 40); do
  if HEALTH="$(curl -fsS http://127.0.0.1:8000/health 2>/dev/null)"; then break; fi
  sleep 1
done
test -n "$HEALTH"
echo "[m1-gate] health response: $HEALTH"
echo "$HEALTH" | python3 -c 'import json, sys; assert json.load(sys.stdin)["status"] == "ok"'

echo "[m1-gate] checking frontend"
FRONTEND_OK=0
for i in $(seq 1 40); do
  if curl -fsS -o /dev/null http://127.0.0.1:5173/ 2>/dev/null; then FRONTEND_OK=1; break; fi
  sleep 1
done
test "$FRONTEND_OK" -eq 1

echo "[m1-gate] PASS: migration exited 0, backend healthy, frontend responded"
