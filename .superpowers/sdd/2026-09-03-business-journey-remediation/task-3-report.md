# Task 3 report

## Status

Implemented corpus byte verification and canonical seven-document journey
binding. Replaced every provisional journey mapping with a deterministic,
executable focused target; MCP transport timeouts now become typed
`MCP_TIMEOUT` failures.

## RED/GREEN evidence

RED: `python -m pytest test_data/runtime/test_journey_manifest.py test_data/runtime/test_fixture_manifest.py -q`
initially reported 8 expected failures for valid-format input/source/hash-map
drift, case-matrix drift, provisional mappings, and stale checked-in README
metadata. `cd backend && python -m pytest tests/runtime/test_journey_case_contracts.py -q`
then reported the expected untyped MCP timeout failure.

GREEN:

- `python test_data/runtime/generate_runtime_fixtures.py --seed 20260826 --output test_data/runtime --check` — OK.
- `python -m pytest test_data/runtime/test_journey_manifest.py test_data/runtime/test_fixture_manifest.py -q` — 29 passed.
- `cd backend && python -m pytest tests/runtime/test_journey_case_contracts.py -q` — 6 passed.
- `cd backend && python -m pytest tests/agent/test_mcp_client.py -q` — 9 passed.
- `cd backend && python -m pytest tests/runtime/test_registered_case_execution.py -q` — 3 passed.

## Registered runner

`cd backend && python -m tests.runtime.run_registered_cases --manifest
../test_data/runtime/manifest.json --report /tmp/task3-registered-cases.json`
completed with a sanitized report: 109 results, zero missing/duplicate cases,
and zero model calls. It did not pass overall because seven pre-existing
generic registry targets fail or skip locally: the two database dialect
targets and three execution row-count targets were skipped, while the three
parity targets exited 4. The six new journey-contract targets passed inside
that same runner invocation.

## Concerns

The backend pytest configuration emits pre-existing SQLAlchemy table-cycle,
Pydantic deprecation, and pytest configuration warnings. The focused suite
itself has no skips.
