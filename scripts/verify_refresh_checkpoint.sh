#!/usr/bin/env bash
# Real mid-milestone refresh checkpoint: fresh Compose + migration, a durable
# batch run and a durable signed webhook run, each independently polled to a
# succeeded DatasetVersion outcome.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CHECKPOINT_PROJECT="ontexus-refresh-checkpoint-$$"
ENV_BACKUP=""
CHECKPOINT_COMPLETE=0

cleanup() {
  local status=$?
  local cleanup_status=0
  echo "[refresh-checkpoint] cleaning up (exit code so far: $status)"
  if docker compose -p "$CHECKPOINT_PROJECT" -f docker-compose.v2.yml --profile refresh down -v --remove-orphans >/dev/null 2>&1; then
    :
  else
    cleanup_status=$?
    echo "[refresh-checkpoint] cleanup failed (docker compose down exit: $cleanup_status)" >&2
  fi
  if [ -n "$ENV_BACKUP" ]; then
    if mv -f "$ENV_BACKUP" .env; then
      echo "[refresh-checkpoint] restored original .env"
    else
      if [ "$cleanup_status" -eq 0 ]; then cleanup_status=1; fi
      echo "[refresh-checkpoint] cleanup failed (could not restore .env)" >&2
    fi
  elif [ -f .env ]; then
    if rm -f .env; then
      echo "[refresh-checkpoint] removed the .env this run created (none existed before)"
    else
      if [ "$cleanup_status" -eq 0 ]; then cleanup_status=1; fi
      echo "[refresh-checkpoint] cleanup failed (could not remove .env)" >&2
    fi
  fi
  if [ "$status" -eq 0 ] && [ "$cleanup_status" -ne 0 ]; then
    status="$cleanup_status"
  fi
  if [ "$status" -eq 0 ] && [ "$CHECKPOINT_COMPLETE" -eq 1 ]; then
    echo "[refresh-checkpoint] PASS: independent batch and signed webhook refreshes succeeded"
  fi
  exit "$status"
}
trap cleanup EXIT

if [ -f .env ]; then
  ENV_BACKUP="$(mktemp)"
  cp .env "$ENV_BACKUP"
  echo "[refresh-checkpoint] backed up existing .env"
fi
cp .env.example .env

echo "[refresh-checkpoint] starting a unique Compose project with refresh workers"
docker compose -p "$CHECKPOINT_PROJECT" -f docker-compose.v2.yml --profile refresh down -v --remove-orphans >/dev/null 2>&1 || true
docker compose -p "$CHECKPOINT_PROJECT" -f docker-compose.v2.yml --profile refresh up --build -d --wait

MIGRATION_ID="$(docker compose -p "$CHECKPOINT_PROJECT" -f docker-compose.v2.yml ps -a -q migration)"
test -n "$MIGRATION_ID"
MIGRATION_STATUS="$(docker inspect -f '{{.State.Status}}' "$MIGRATION_ID")"
MIGRATION_EXIT="$(docker inspect -f '{{.State.ExitCode}}' "$MIGRATION_ID")"
echo "[refresh-checkpoint] migration container: status=$MIGRATION_STATUS exit=$MIGRATION_EXIT"
test "$MIGRATION_STATUS" = "exited"
test "$MIGRATION_EXIT" = "0"

# --wait does not include an application healthcheck, so wait for the API
# before seeding or sending either live request.
API_READY=0
for i in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then API_READY=1; break; fi
  sleep 1
done
test "$API_READY" -eq 1

# The seed command emits one canonical JSON line. Keep its sensitive values in
# shell variables only; never echo the token or webhook secret.
FIXTURE_BODY="$REPO_ROOT/test_data/runtime/fixtures/checkpoint_webhook_body.json"
SEED_JSON="$(docker compose -p "$CHECKPOINT_PROJECT" -f docker-compose.v2.yml exec -T backend python scripts/seed_refresh_checkpoint_fixture.py < "$FIXTURE_BODY")"
OPERATOR_TOKEN="$(printf '%s' "$SEED_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["operator_token"])')"
WEBHOOK_SIGNATURE="$(printf '%s' "$SEED_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["webhook_signature"])')"
AUTH_HEADER="Authorization: Bearer $OPERATOR_TOKEN"
WEBHOOK_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

BATCH_RESPONSE="$(curl -fsS -X POST \
  -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
  --data '{"mode":"batch"}' \
  http://127.0.0.1:8000/api/v2/refresh/sources/source-checkpoint-batch/run)"
printf '%s' "$BATCH_RESPONSE" | python3 -c 'import json,sys; body=json.load(sys.stdin); assert body.get("run_id")'

EVENT_RESPONSE="$(curl -fsS -X POST \
  -H "$AUTH_HEADER" \
  -H "X-Webhook-Signature: $WEBHOOK_SIGNATURE" \
  -H "X-Webhook-Timestamp: $WEBHOOK_TIMESTAMP" \
  --data-binary "@$FIXTURE_BODY" \
  http://127.0.0.1:8000/api/v2/refresh/events/webhook/source-checkpoint-event)"
printf '%s' "$EVENT_RESPONSE" | python3 -c 'import json,sys; body=json.load(sys.stdin); assert body.get("run_id")'

poll_source() {
  local source_id="$1"
  local deadline=$((SECONDS + 40))
  local last_status=""
  local response=""
  while (( SECONDS < deadline )); do
    response="$(curl -fsS \
      -H "$AUTH_HEADER" \
      "http://127.0.0.1:8000/api/v2/refresh/sources/$source_id/status")"
    last_status="$(printf '%s' "$response" | python3 -c 'import json,sys; body=json.load(sys.stdin); run=body.get("latest_run") or {}; print(run.get("status", "missing"))')"
    if printf '%s' "$response" | python3 -c 'import json,sys; body=json.load(sys.stdin); run=body.get("latest_run") or {}; assert run.get("status") == "succeeded" and run.get("input_dataset_version_ids") and run.get("pipeline_run_id")' >/dev/null 2>&1; then
      echo "[refresh-checkpoint] $source_id reached succeeded with durable DatasetVersion"
      return 0
    fi
    sleep 1
  done
  echo "[refresh-checkpoint] timeout waiting for $source_id (last status: $last_status)" >&2
  return 1
}

# Poll each source independently and never reuse one source's latest_run.
poll_source "source-checkpoint-batch"
poll_source "source-checkpoint-event"

CHECKPOINT_COMPLETE=1
