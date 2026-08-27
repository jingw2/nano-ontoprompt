# Task 10 report — refresh operations and live checkpoint

Date: 2026-08-28
Worktree: `.worktrees/real-model-business-journeys`
Task: 10 — Expose refresh operations and failure/replay status

## Outcome

Task 10 is implemented and verified. The authenticated refresh API persists
durable source/run/schedule/cancellation/replay state and publishes only
run-ID-only refresh dispatch messages. Connector pulls, event materialization,
replay execution, and cancellation finalization remain worker responsibilities.

The required live Compose checkpoint passed on the final tree: a freshly
migrated database and explicit `--profile refresh` workers processed one real
batch trigger and one real signed webhook. Independent status polling observed
both sources reach `succeeded` with a durable DatasetVersion and PipelineRun
lineage. The script cleanup completed; a post-run Docker inspection found no
checkpoint containers, volumes, or networks, and the pre-existing `.env` was
restored.

## TDD evidence

RED evidence was preserved before production implementation:

1. `cd backend && python -m pytest tests/v2/incremental/test_refresh_api.py -q`
   — **12 failed**, 20 warnings. The new API tests failed because the
   operations module/routes did not yet exist (404s/import failures).
2. `cd backend && python -m pytest tests/v2/incremental/test_refresh_api.py tests/v2/incremental/test_refresh_cancellation.py::test_refresh_operations_cancel_adapter_preserves_operator_identity tests/v2/incremental/test_refresh_task_dispatch.py::test_refresh_replay_task_is_registered_as_a_run_id_only_worker -q`
   — **13 passed, 1 failed**. The remaining intentional RED was the absent
   `refresh_replay_task` registration.
3. `cd backend && python -m pytest tests/v2/incremental/test_refresh_checkpoint_gate.py -q`
   — **3 failed**, because the checkpoint script/workflow wiring had not yet
   been added.
4. After the inherited gate passed once, the cleanup-safety regression test
   was run against the inherited script and failed because cleanup errors were
   ignored and the PASS line ran before teardown.

After implementation, the final focused suite passed:

```text
cd backend && python -m pytest \
  tests/v2/incremental/test_refresh_checkpoint_gate.py \
  tests/v2/incremental/test_refresh_api.py \
  tests/v2/incremental/test_refresh_cancellation.py \
  tests/v2/incremental/test_refresh_task_dispatch.py \
  tests/v2/incremental/test_refresh_schedule.py \
  tests/v2/incremental/test_refresh_polling.py \
  tests/v2/incremental/test_event_ingest.py -q
85 passed, 1 skipped, 94 warnings in 13.60s
```

The one skip is the existing SQLite-only limitation for the concurrent
database-lock cancellation race. Warnings are existing framework/deprecation
and metadata-cycle warnings; they did not fail the suite.

The complete incremental package was also rerun after the cleanup fix:
`python -m pytest tests/v2/incremental -q` — **109 passed, 20 skipped, 137
warnings in 17.28s**. The skips are the existing optional PostgreSQL/MySQL
runtime-fixture checks and the SQLite concurrency limitation.

Additional final checks:

```text
python -m py_compile \
  backend/app/services/v2/incremental/operations.py \
  backend/app/routers/v2/refresh.py \
  backend/scripts/seed_refresh_checkpoint_fixture.py \
  backend/app/tasks/v2/refresh_tasks.py
bash -n scripts/verify_refresh_checkpoint.sh
git diff --check
```

All completed successfully.

## Live checkpoint evidence

Command (run from the repository root):

```text
bash scripts/verify_refresh_checkpoint.sh
```

The final run used unique Compose project `ontexus-refresh-checkpoint-43826`
and the command included `docker compose ... --profile refresh up --build -d
--wait`. The migration container reported `status=exited exit=0`. The script
then:

- seeded `source-checkpoint-batch` and `source-checkpoint-event` in that
  freshly migrated database;
- captured the seed CLI's canonical JSON in shell variables without echoing
  the operator token or webhook secret;
- sent a real `POST /api/v2/refresh/sources/source-checkpoint-batch/run`;
- sent the fixed fixture bytes to the real signed
  `POST /api/v2/refresh/events/webhook/source-checkpoint-event`;
- polled each source independently at
  `GET /api/v2/refresh/sources/{source_id}/status` until each reported
  `succeeded` with non-empty DatasetVersion and PipelineRun identifiers.

Observed terminal lines (sensitive values omitted):

```text
[refresh-checkpoint] migration container: status=exited exit=0
[refresh-checkpoint] source-checkpoint-batch reached succeeded with durable DatasetVersion
[refresh-checkpoint] source-checkpoint-event reached succeeded with durable DatasetVersion
[refresh-checkpoint] cleaning up (exit code so far: 0)
[refresh-checkpoint] restored original .env
[refresh-checkpoint] PASS: independent batch and signed webhook refreshes succeeded
```

The first live attempt exposed a container invocation-path issue
(`ModuleNotFoundError: No module named 'app'`), fixed by making the seed CLI
resolve the mounted backend package. The next attempt exposed that the
repository-level fixture is not mounted into the backend container
(`FileNotFoundError: /test_data/...`); the checkpoint now streams the exact
fixture bytes to the seed CLI over stdin. A post-run check also found that
profiled worker containers were not removed when cleanup omitted the profile;
the trap and stale-project teardown now include `--profile refresh`. The
inherited script also ignored teardown errors and printed PASS before the EXIT
trap. The final script tracks teardown and `.env` restoration errors, only
prints PASS after successful cleanup, and the final run's explicit inspection
confirmed zero matching containers/volumes/networks remained.

## Implemented behavior

- Typed, editor-authorized operations for source status, bounded manual
  trigger/backfill, cancellation, failure/DLQ replay, schedule updates, and
  sanitized health/queue readiness.
- Server-owned source resource/cursor/configuration resolution and
  idempotency keyed by source/resource/config revision; caller-supplied
  cursor, URL, credential, broker, payload, lease owner, and fencing values
  are rejected or ignored.
- Durable run commit before enqueue; publish failures remain recoverable in
  the queued state. Replay dispatch uses the dedicated `refresh.replay` queue
  and task receives only the durable run ID.
- Task 6 cancellation semantics preserved, including plain
  `already_terminal=True` responses for all terminal statuses.
- Existing connection schedule endpoint retained as a compatibility delegate
  to the typed persisted schedule operation.
- CI wiring for the standalone checkpoint with failure log upload.
- Checkpoint cleanup is fail-closed: teardown or `.env` restoration failures
  produce a nonzero result and suppress the PASS line.
- Checkpoint seed uses the existing application JWT/editor credential
  mechanism only; it does not introduce the Task 13 Runtime delegation or
  identity subsystem.

## Changed files

```text
.github/workflows/agent-mvp.yml
backend/app/main.py
backend/app/routers/v2/connections.py
backend/app/routers/v2/refresh.py
backend/app/services/v2/incremental/operations.py
backend/app/tasks/v2/refresh_tasks.py
backend/scripts/seed_refresh_checkpoint_fixture.py
backend/tests/v2/incremental/test_refresh_api.py
backend/tests/v2/incremental/test_refresh_cancellation.py
backend/tests/v2/incremental/test_refresh_checkpoint_gate.py
backend/tests/v2/incremental/test_refresh_task_dispatch.py
scripts/verify_refresh_checkpoint.sh
test_data/runtime/fixtures/checkpoint_webhook_body.json
```

The inherited untracked `backend/uv.lock` was intentionally not changed or
staged.

## Self-review and remaining concerns

- The refresh health endpoint currently receives sanitized zero-depth,
  worker-ready observations from the API adapter. Durable run lag/retry/DLQ
  metrics are real, while live broker inspection remains an integration point
  for a future broker-observer implementation.
- The existing application JWT mechanism is reused for this checkpoint and
  editor authorization. Task 13 remains the authority for Runtime delegated
  identity/scopes; no Task 13 subsystem is claimed here.
- Focused tests retain the pre-existing warning set and one SQLite concurrency
  skip described above.
- No connector payload, credentials, or source configuration is placed in a
  broker message; the live checkpoint intentionally uses synthetic,
  non-PII fixture values only.
