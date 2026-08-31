# Task 28 release-gate report

## Implemented

- Added the typed deterministic registry runner at
  `backend/tests/runtime/run_registered_cases.py`. It executes exactly one
  registered pytest node per deterministic case in a subprocess, excludes
  `real_model_browser` cases, records zero model calls, and writes a
  sanitized report containing no test stdout/stderr.
- Added the acceptance matrix and runner contracts under
  `backend/tests/runtime/`.
- Made registry validation fail closed for refresh lineage outcomes,
  cancellation no-progress behavior, and the explicit ordinary
  `cancel-already-terminal` case.
- Reconciled `fixtures/refresh_cases.json` with the manifest: each refresh
  fixture now includes refresh mode, source contract, cursor/lineage outcomes,
  and cancellation/terminal metadata.
- Wired deterministic fixtures, registry, Runtime/refresh/queue suites, SDK,
  frontend governance E2E, and both application Compose validations into CI.
  The existing Task 10 `scripts/verify_refresh_checkpoint.sh` remains the
  sole refresh Compose/seed/webhook/poll implementation and is called by its
  existing CI step.

## Commands and results

Passed:

```text
python test_data/runtime/generate_runtime_fixtures.py --seed 20260826 --output test_data/runtime --check
# OK: test_data/runtime matches a fresh generation with seed 20260826

python -m pytest test_data/runtime/test_fixture_manifest.py -q
# 4 passed

cd backend && python -m pytest tests/runtime/test_registered_case_execution.py -q
# 3 passed

cd backend && python -m pytest tests/runtime/test_registered_case_execution.py tests/runtime/test_acceptance_matrix.py -q -k 'not registry_runner'
# 76 passed, 1 deselected
```

The full runner is intentionally blocked by stale registry targets that were
already present before Task 28. Direct collection demonstrated, for example:

```text
cd backend && python -m pytest tests/v2/incremental/test_refresh_schedule.py::test_bounded_backfill_window_advances_cursor -q
# ERROR: node not found
```

The registry also refers to absent files including
`backend/tests/runtime/test_snapshot_lineage.py`,
`test_identity_credentials.py`, `test_execution_governance.py`, and
`integration/test_managed_row_writer_dialects.py`. The new runner correctly
returns a failed deterministic result for such nodes; it does not replace them
with unrelated tests or claim release coverage. No Playwright, live Compose,
or real-model business-journey execution was claimed from this local run.

## Environment notes

- The provided Task 28 brief and Task 27 report were not present anywhere
  under `/Users/jingwang`; the implementation used the checked-in Task 28
  specification in
  `docs/superpowers/plans/2026-08-26-agent-semantic-infrastructure-implementation.md`.
- Full Compose and browser gates require their configured Docker/browser
  services and were wired but not represented as passing locally.
