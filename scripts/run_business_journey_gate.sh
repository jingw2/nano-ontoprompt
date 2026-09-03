#!/usr/bin/env bash
# =============================================================================
# Business-journey real gate (plan 2026-08-27, Task 5).
#
# A single fail-fast gate for trusted pull requests only: runs the
# deterministic fixture/registry suite with zero model calls, prepares all
# three real DeepSeek-backed journey baselines, drives the exact three
# strict Playwright journeys against a fresh disposable stack, verifies the
# persisted post-browser evidence read-only, and scans/stages only sanitized
# evidence. `set -euo pipefail` and no per-step error suppression anywhere
# in this script: any phase failing stops the gate immediately.
#
# Accepts exactly:
#   DEEPSEEK_API_KEY         required -- the real provider credential. This
#                             script never accepts or reads a DeepSeek
#                             endpoint/base-URL override; the client always
#                             talks to the one official DeepSeek origin.
#   BUSINESS_JOURNEY_API_BASE  optional -- the application-under-test base
#                             URL only. Defaults to the disposable stack
#                             this script itself starts.
#
# Creates exactly one uniquely-named disposable Compose project and tears
# down only that project (`down -v --remove-orphans`), on success or
# failure, mirroring `scripts/verify_m1_stabilization_gate.sh`'s own
# backup/restore-.env and trap-cleanup pattern.
#
# Usage (from the repo root, with a trusted, same-repository DEEPSEEK_API_KEY):
#   DEEPSEEK_API_KEY=... bash scripts/run_business_journey_gate.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "DEEPSEEK_API_KEY_REQUIRED: set a real DeepSeek credential before running this gate" >&2
  exit 2
fi

RUN_ID="business-journey-$(date +%s)-$$"
PROJECT="business-journey-gate-$$"
API_BASE="${BUSINESS_JOURNEY_API_BASE:-http://127.0.0.1:8000}"
ARTIFACTS_DIR="$REPO_ROOT/artifacts"
STAGING_DIR="$REPO_ROOT/artifacts/business_journeys/staging"

ENV_BACKUP=""

cleanup() {
  local status=$?
  echo "[business-journey-gate] cleaning up (exit code so far: $status)"
  docker compose -p "$PROJECT" -f docker-compose.v2.yml down -v --remove-orphans >/dev/null 2>&1 || true
  if [ -n "$ENV_BACKUP" ]; then
    mv -f "$ENV_BACKUP" .env
    echo "[business-journey-gate] restored original .env"
  elif [ -f .env ]; then
    rm -f .env
    echo "[business-journey-gate] removed the .env this run created (none existed before)"
  fi
  exit "$status"
}
trap cleanup EXIT

# .env is real developer config (git-ignored); use .env.example's
# Docker-correct defaults for this run only, always restoring whatever was
# there before -- same discipline as the M1 stabilization gate.
if [ -f .env ]; then
  ENV_BACKUP="$(mktemp)"
  cp .env "$ENV_BACKUP"
  echo "[business-journey-gate] backed up existing .env"
fi
cp .env.example .env
# The ONLY switch that lets a real Agent turn take the privileged,
# no-approval-gate business-journey Runtime protocol (backend/app/config.py).
# Never true in a real deployment; only ever true for this controlled gate.
echo "BUSINESS_JOURNEY_ACCEPTANCE_ENABLED=true" >> .env

echo "[business-journey-gate] tearing down any stale state for $PROJECT"
docker compose -p "$PROJECT" -f docker-compose.v2.yml down -v --remove-orphans >/dev/null 2>&1 || true

echo "[business-journey-gate] starting disposable API/worker/frontend stack: $PROJECT"
COMPOSE_PROFILES=agent docker compose -p "$PROJECT" -f docker-compose.v2.yml up --build -d --wait

echo "[business-journey-gate] waiting for backend health"
HEALTH=""
for i in $(seq 1 60); do
  if HEALTH="$(curl -fsS "${API_BASE}/health" 2>/dev/null)"; then break; fi
  sleep 1
done
test -n "$HEALTH"
echo "$HEALTH" | python3 -c 'import json, sys; assert json.load(sys.stdin)["status"] == "ok"'
echo "[business-journey-gate] backend healthy"

echo "[business-journey-gate] phase 1/6: fixture generation/check (zero model calls)"
python test_data/runtime/generate_runtime_fixtures.py --seed 20260826 --output test_data/runtime --check

echo "[business-journey-gate] phase 2/6: deterministic registry (zero model calls)"
(cd backend && python -m tests.runtime.run_registered_cases \
    --manifest "$REPO_ROOT/test_data/runtime/manifest.json" \
    --report "$REPO_ROOT/artifacts/runtime/deterministic-cases.json")

echo "[business-journey-gate] phase 3/6: prepare all three journeys (real DeepSeek)"
(cd backend && python -m evals.business_journeys.run \
    --phase prepare --journey all --model-id deepseek-v4-flash-vision-exp \
    --api-base "$API_BASE" \
    --output "$ARTIFACTS_DIR" --run-id "$RUN_ID")

export BUSINESS_JOURNEY_RUN_MANIFEST="$STAGING_DIR/run.json"

echo "[business-journey-gate] phase 4/6: exact three browser journeys"
(cd frontend && AGENT_E2E_API_BASE="$API_BASE" npx playwright test --config playwright.config.ts \
    src/test/e2e/business-journeys.spec.ts)

echo "[business-journey-gate] phase 5/6: read-only post-browser verification"
(cd backend && python -m evals.business_journeys.run \
    --phase verify --journey all --api-base "$API_BASE" \
    --output "$ARTIFACTS_DIR" --run-id "$RUN_ID")

echo "[business-journey-gate] phase 6/6: scan and materialize sanitized evidence"
(cd backend && python -m evals.business_journeys.artifacts \
    scan --manifest "$REPO_ROOT/test_data/runtime/manifest.json" \
    --staging "$STAGING_DIR" \
    --sanitized-output "$REPO_ROOT/artifacts/business_journeys/sanitized" \
    --failure-summary "$ARTIFACTS_DIR/business_journeys/scanner-failure-summary.json" \
    --deterministic-report "$ARTIFACTS_DIR/runtime/deterministic-cases.json" \
    --run-id "$RUN_ID")

echo "[business-journey-gate] PASS: all six phases completed for $RUN_ID"
