#!/usr/bin/env python3
"""Deterministic generator for the runtime test-data corpus.

Produces the generic (non-business-journey) fixture corpus that later
Milestone 2/3 tasks register cases into: snapshot, identity, runtime,
execution, transport/parity, refresh (incl. cancellation), database, and the
three business-journey placeholder cases. Everything this script writes is
canonical JSON/SQL/YAML/Markdown text derived only from fixed, synthetic
data -- no randomness, no real credentials, no PII.

CLI:
    python generate_runtime_fixtures.py --seed 20260826 --output <dir> [--check]

Library:
    generate(seed: int, output_dir: Path) -> dict
    validate_manifest(output_dir: Path) -> None
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registry  # noqa: E402

MANIFEST_VERSION = 1
FIXED_NOW = "2026-08-26T00:00:00Z"
TENANTS = ["tenant-acme", "tenant-beta"]
REQUIRED_CASE_FIELDS = ("case_id", "expected", "coverage", "layers", "test_targets")

# ---------------------------------------------------------------------------
# Case catalog
#
# Each entry supplies everything about a case *except* its test target,
# which is looked up from registry.CASE_REGISTRY by case_id so the manifest
# and the registry always start in sync.
# ---------------------------------------------------------------------------


def _tenant(index: int) -> str:
    return TENANTS[index % len(TENANTS)]


def _case(case_id: str, layers: list[str], expected: dict, coverage: list[str], index: int, **extra: Any) -> dict:
    case: dict[str, Any] = {
        "case_id": case_id,
        "layers": layers,
        "expected": expected,
        "coverage": coverage,
        "tenant_id": _tenant(index),
        "user_id": f"user-{_tenant(index).split('-')[1]}-operator-{index:02d}",
    }
    case.update(extra)
    return case


def _snapshot_cases() -> list[dict]:
    defs = [
        ("snapshot-valid-published-release", {"decision": "ALLOW", "lineage_complete": True}),
        ("snapshot-authorized-empty-result", {"decision": "ALLOW", "row_count": 0}),
        ("snapshot-incomplete-lineage", {"decision": "DENY", "reason_code": "INCOMPLETE_LINEAGE"}),
        ("snapshot-failed-ungoverned-input", {"decision": "DENY", "reason_code": "UNGOVERNED_PIPELINE_INPUT"}),
        ("snapshot-stale-snapshot", {"decision": "DENY", "reason_code": "STALE_SNAPSHOT"}),
        ("snapshot-release-drift", {"decision": "DENY", "reason_code": "RELEASE_DRIFT"}),
        ("snapshot-reordered-input-lists", {"decision": "ALLOW", "hash_order_independent": True}),
    ]
    return [
        _case(case_id, ["snapshot"], expected, ["snapshot.lineage"], i)
        for i, (case_id, expected) in enumerate(defs)
    ]


def _identity_cases() -> list[dict]:
    defs = [
        ("identity-valid-delegation", {"decision": "ALLOW"}),
        ("identity-missing-credential", {"decision": "DENY", "reason_code": "MISSING_CREDENTIAL"}),
        ("identity-malformed-signature", {"decision": "DENY", "reason_code": "MALFORMED_SIGNATURE"}),
        ("identity-wrong-audience", {"decision": "DENY", "reason_code": "WRONG_AUDIENCE"}),
        ("identity-missing-scope", {"decision": "DENY", "reason_code": "MISSING_SCOPE"}),
        ("identity-expired-token", {"decision": "DENY", "reason_code": "EXPIRED_TOKEN"}),
        ("identity-revoked-token", {"decision": "DENY", "reason_code": "REVOKED_TOKEN"}),
        ("identity-inactive-agent", {"decision": "DENY", "reason_code": "INACTIVE_AGENT"}),
        ("identity-inactive-user", {"decision": "DENY", "reason_code": "INACTIVE_USER"}),
        ("identity-cross-domain-token", {"decision": "DENY", "reason_code": "CROSS_DOMAIN_TOKEN"}),
        ("identity-agent-only-capability", {"decision": "DENY", "reason_code": "MISSING_USER_ENTITLEMENT"}),
        ("identity-user-only-entitlement", {"decision": "DENY", "reason_code": "MISSING_AGENT_CAPABILITY"}),
    ]
    return [
        _case(case_id, ["identity"], expected, ["identity.delegation"], i)
        for i, (case_id, expected) in enumerate(defs)
    ]


def _runtime_cases() -> list[dict]:
    defs = [
        ("runtime-evidence-citations", {"decision": "ALLOW", "citation_count_min": 1}),
        ("runtime-rule-outcomes", {"decision": "ALLOW", "rule_outcome": "matched"}),
        ("runtime-allow-with-data", {"decision": "ALLOW", "row_count_min": 1}),
        ("runtime-allow-no-matches", {"decision": "ALLOW", "row_count": 0}),
        ("runtime-structured-deny", {"decision": "DENY", "reason_code": "POLICY_DENIAL"}),
        ("runtime-policy-denial", {"decision": "DENY", "reason_code": "POLICY_DENIAL"}),
        ("runtime-immutable-read-only-plan", {"decision": "ALLOW", "plan_writable": False}),
        ("runtime-writable-plan-binding", {"decision": "ALLOW", "plan_writable": True}),
        ("runtime-expired-plan", {"decision": "DENY", "reason_code": "PLAN_EXPIRED"}),
        ("runtime-stable-denial-codes", {"decision": "DENY", "reason_code": "POLICY_DENIAL"}),
    ]
    return [
        _case(case_id, ["runtime"], expected, ["runtime.investigation"], i)
        for i, (case_id, expected) in enumerate(defs)
    ]


def _execution_cases() -> list[dict]:
    defs = [
        ("execution-low-risk-automatic-update", {"outcome": "succeeded", "risk_class": "automatic"}),
        ("execution-high-risk-exact-hash-hitl-update", {"outcome": "pending_approval", "risk_class": "human_approved"}),
        ("execution-ambiguous-rejected-plan", {"outcome": "rejected", "reason_code": "AMBIGUOUS_PLAN"}),
        ("execution-binding-draft-state", {"outcome": "rejected", "reason_code": "BINDING_NOT_PUBLISHED"}),
        ("execution-binding-revoked-state", {"outcome": "rejected", "reason_code": "BINDING_REVOKED"}),
        ("execution-binding-version-drift", {"outcome": "rejected", "reason_code": "BINDING_VERSION_DRIFT"}),
        ("execution-connection-target-drift", {"outcome": "rejected", "reason_code": "CONNECTION_TARGET_DRIFT"}),
        ("execution-parameter-selector-drift", {"outcome": "rejected", "reason_code": "PARAMETER_SELECTOR_DRIFT"}),
        ("execution-before-image-version-conflict", {"outcome": "rejected", "reason_code": "VERSION_CONFLICT"}),
        ("execution-row-count-zero", {"outcome": "rejected", "reason_code": "ROW_COUNT_ZERO"}),
        ("execution-row-count-two", {"outcome": "rejected", "reason_code": "ROW_COUNT_NOT_SINGLE"}),
        ("execution-idempotent-retry", {"outcome": "succeeded", "idempotent": True}),
        ("execution-timeout-unknown-outcome", {"outcome": "unknown", "reconciliation_required": True}),
        ("execution-reconciliation-and-rollback", {"outcome": "rollback_plan_created"}),
    ]
    return [
        _case(case_id, ["execution"], expected, ["execution.governed_writeback"], i)
        for i, (case_id, expected) in enumerate(defs)
    ]


def _parity_cases() -> list[dict]:
    defs = [
        ("parity-allow-with-data", {"decision": "ALLOW", "row_count_min": 1}),
        ("parity-denied-result", {"decision": "DENY", "reason_code": "POLICY_DENIAL"}),
        ("parity-writable-plan-hash", {"decision": "ALLOW", "plan_hash_stable": True}),
    ]
    return [
        _case(
            case_id, ["parity"], expected, ["transport.parity"], i,
            transports=["mcp", "reference-agent", "rest", "sdk"],
        )
        for i, (case_id, expected) in enumerate(defs)
    ]


def _refresh_cases() -> list[dict]:
    # (case_id, refresh_mode, source_contract, cursor_outcome, extra)
    defs = [
        ("normal", "batch", "watermark_primary_key", "advanced", {}),
        ("empty", "batch", "watermark_primary_key", "unchanged", {}),
        ("late", "micro_batch", "watermark_primary_key", "advanced", {}),
        ("equal-watermark", "micro_batch", "watermark_primary_key", "advanced", {}),
        ("duplicate", "batch", "watermark_primary_key", "advanced", {}),
        ("out-of-order", "micro_batch", "watermark_primary_key", "advanced", {}),
        ("failed-retry", "batch", "watermark_primary_key", "unchanged", {}),
        ("cursor-nonadvance", "micro_batch", "opaque_source_cursor", "unchanged", {}),
        ("dlq-replay", "batch", "watermark_primary_key", "dead_lettered", {}),
        ("expired-webhook", "event_driven", "opaque_source_cursor", "unchanged", {}),
        ("replay-attack", "event_driven", "opaque_source_cursor", "unchanged", {}),
        ("schema-drift", "event_driven", "opaque_source_cursor", "unchanged", {}),
        (
            "config-drift-late-finish",
            "batch",
            "watermark_primary_key",
            "unchanged",
            {"error_code": "CONFIGURATION_DRIFT"},
        ),
        ("backfill", "batch", "watermark_primary_key", "advanced", {}),
        ("t1-timezone", "batch", "watermark_primary_key", "advanced", {}),
        ("cancel-before-pull", "micro_batch", "watermark_primary_key", "unchanged", {"cancel_outcome": "cancelled"}),
        ("cancel-inflight-page", "micro_batch", "watermark_primary_key", "unchanged", {"cancel_outcome": "cancelled"}),
        (
            "cancel-after-tentative-materialization",
            "micro_batch",
            "watermark_primary_key",
            "unchanged",
            {"cancel_outcome": "cancelled"},
        ),
    ]
    cases = []
    for i, (case_id, refresh_mode, source_contract, cursor_outcome, extra) in enumerate(defs):
        layers = ["refresh"]
        if case_id.startswith("cancel-"):
            layers.append("cancellation")
        expected = {"cursor_outcome": cursor_outcome}
        if "error_code" in extra:
            expected["error_code"] = extra["error_code"]
        if "cancel_outcome" in extra:
            expected["cancel_outcome"] = extra["cancel_outcome"]
        cases.append(
            _case(
                case_id,
                layers,
                expected,
                ["refresh.cursor_contract"],
                i,
                refresh_mode=refresh_mode,
                source_contract=source_contract,
                cursor_outcome=cursor_outcome,
                **extra,
            )
        )
    return cases


def _database_cases() -> list[dict]:
    defs = [
        (
            "database-row-parity",
            {"row_count": 1, "optimistic_lock": "row_version_incremented"},
        ),
        (
            "database-target-row-drift",
            {"outcome": "rejected", "reason_code": "VERSION_CONFLICT"},
        ),
    ]
    return [
        _case(
            case_id, ["database"], expected, ["database.dialect_parity"], i,
            dialects=["mysql", "postgresql"],
        )
        for i, (case_id, expected) in enumerate(defs)
    ]


def _journey_cases(seed: int) -> list[dict]:
    defs = [
        ("journey-supply-chain", "supply_chain", "automatic"),
        ("journey-finance", "finance", "human_approved"),
        ("journey-credit", "credit", "rejected"),
    ]
    cases = []
    for i, (case_id, journey_id, risk_class) in enumerate(defs):
        fixture_hash = hashlib.sha256(f"{seed}:{case_id}:{journey_id}".encode("utf-8")).hexdigest()
        cases.append(
            _case(
                case_id,
                ["business_journey"],
                {"result": "governed_browser_loop_completed"},
                ["business_journey.three_journey_acceptance"],
                i,
                journey_id=journey_id,
                model_id=registry.JOURNEY_MODEL_ID,
                skip_allowed=False,
                fixture_manifest_sha256=fixture_hash,
                risk_class=risk_class,
            )
        )
    return cases


def build_case_defs(seed: int) -> list[dict]:
    cases = (
        _snapshot_cases()
        + _identity_cases()
        + _runtime_cases()
        + _execution_cases()
        + _parity_cases()
        + _refresh_cases()
        + _database_cases()
        + _journey_cases(seed)
    )
    seen: set[str] = set()
    for case in cases:
        case_id = case["case_id"]
        if case_id in seen:
            raise ValueError(f"duplicate case_id in catalog: {case_id!r}")
        seen.add(case_id)
    return cases


def _finalize_case(case: dict) -> dict:
    """Stamp execution_mode and test_targets from the registry, and validate."""

    case_id = case["case_id"]
    targets = registry.targets_for(case_id)
    case = dict(case)
    case["execution_mode"] = "real_model_browser" if "business_journey" in case["layers"] else "deterministic"
    case["test_targets"] = [t.to_dict() for t in targets]
    for field in REQUIRED_CASE_FIELDS:
        if not case.get(field):
            raise ValueError(f"generated case {case_id!r} is missing required field {field!r}")
    registry.assert_case_registry(case)
    return case


# ---------------------------------------------------------------------------
# Fixture content files (underlying synthetic data per case, by layer)
# ---------------------------------------------------------------------------


def _fixture_records(cases: list[dict], layer: str) -> dict:
    records = {}
    for case in cases:
        if layer not in case["layers"]:
            continue
        records[case["case_id"]] = {
            "case_id": case["case_id"],
            "tenant_id": case["tenant_id"],
            "user_id": case["user_id"],
            "expected": case["expected"],
        }
    return records


def build_fixture_files(cases: list[dict]) -> dict[str, dict]:
    return {
        "fixtures/snapshots.json": {"cases": _fixture_records(cases, "snapshot")},
        "fixtures/identities.json": {"cases": _fixture_records(cases, "identity")},
        "fixtures/runtime_cases.json": {"cases": _fixture_records(cases, "runtime")},
        "fixtures/execution_cases.json": {
            "cases": {
                **_fixture_records(cases, "execution"),
                **_fixture_records(cases, "database"),
            }
        },
        "fixtures/transport_cases.json": {"cases": _fixture_records(cases, "parity")},
        "fixtures/refresh_cases.json": {"cases": _fixture_records(cases, "refresh")},
    }


def build_playwright_seed() -> dict:
    return {
        "schedule": {
            "source_id": "source-acme-erp",
            "cron_expr": "0 2 * * *",
            "timezone": "Asia/Shanghai",
            "next_due_at": "2026-08-27T02:00:00Z",
        },
        "cursor": {
            "source_id": "source-acme-erp",
            "watermark": "2026-08-25T23:00:00Z",
            "primary_key": "row-acme-000042",
        },
        "config_version": {"source_id": "source-acme-erp", "version": 3},
        "lag": {"source_id": "source-acme-erp", "source_lag_seconds": 42},
        "cancellation": {
            "requested": {"run_id": "run-acme-000101", "status": "cancel_requested"},
            "cancelled": {"run_id": "run-acme-000102", "status": "cancelled"},
            "already_terminal": {
                "run_id": "run-acme-000103",
                "status": "succeeded",
                "already_terminal": True,
            },
        },
        "dead_letter": {"dead_letter_id": "dlq-acme-000001", "reason": "max_retries_exceeded"},
        "replay": {"replay_run_id": "run-acme-000104", "source_dead_letter_id": "dlq-acme-000001"},
        "configuration_drift": {"run_id": "run-acme-000105", "error_code": "CONFIGURATION_DRIFT"},
    }


# ---------------------------------------------------------------------------
# Database (PostgreSQL / MySQL) fixture SQL and Compose file
# ---------------------------------------------------------------------------


def build_postgres_schema() -> str:
    return """-- Synthetic PostgreSQL schema for the runtime fixture corpus.
-- Test infrastructure only. Never used against a production database.

CREATE TABLE managed_targets (
    target_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE refresh_source_rows (
    row_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    watermark TIMESTAMPTZ NOT NULL,
    sequence_no INTEGER NOT NULL,
    payload_summary VARCHAR(128) NOT NULL
);

CREATE TABLE refresh_event_log (
    event_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    cursor_value VARCHAR(64) NOT NULL,
    sequence_no INTEGER NOT NULL,
    received_at TIMESTAMPTZ NOT NULL
);
"""


def build_postgres_seed() -> str:
    return """-- Synthetic PostgreSQL seed rows. Test infrastructure only.

INSERT INTO managed_targets (target_id, tenant_id, status, row_version, updated_at) VALUES
    ('target-acme-000001', 'tenant-acme', 'active', 1, '2026-08-26T00:00:00Z'),
    ('target-beta-000001', 'tenant-beta', 'active', 1, '2026-08-26T00:00:00Z');

INSERT INTO refresh_source_rows (row_id, source_id, resource, watermark, sequence_no, payload_summary) VALUES
    ('row-acme-000001', 'source-acme-erp', 'purchase_orders', '2026-08-25T22:00:00Z', 1, 'normal batch row'),
    ('row-acme-000002', 'source-acme-erp', 'purchase_orders', '2026-08-25T23:00:00Z', 2, 'late-arriving row'),
    ('row-beta-000001', 'source-beta-crm', 'accounts', '2026-08-25T22:30:00Z', 1, 'normal batch row');

INSERT INTO refresh_event_log (event_id, source_id, resource, cursor_value, sequence_no, received_at) VALUES
    ('event-acme-000001', 'source-acme-erp', 'purchase_orders', 'cursor-acme-000001', 1, '2026-08-25T22:05:00Z'),
    ('event-beta-000001', 'source-beta-crm', 'accounts', 'cursor-beta-000001', 1, '2026-08-25T22:35:00Z');
"""


def build_mysql_schema() -> str:
    return """-- Synthetic MySQL schema for the runtime fixture corpus.
-- Test infrastructure only. Never used against a production database.

CREATE TABLE managed_targets (
    target_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    row_version INT NOT NULL DEFAULT 1,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB;

CREATE TABLE refresh_source_rows (
    row_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    watermark DATETIME NOT NULL,
    sequence_no INT NOT NULL,
    payload_summary VARCHAR(128) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE refresh_event_log (
    event_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    cursor_value VARCHAR(64) NOT NULL,
    sequence_no INT NOT NULL,
    received_at DATETIME NOT NULL
) ENGINE=InnoDB;
"""


def build_mysql_seed() -> str:
    return """-- Synthetic MySQL seed rows. Test infrastructure only.

INSERT INTO managed_targets (target_id, tenant_id, status, row_version, updated_at) VALUES
    ('target-acme-000001', 'tenant-acme', 'active', 1, '2026-08-26 00:00:00'),
    ('target-beta-000001', 'tenant-beta', 'active', 1, '2026-08-26 00:00:00');

INSERT INTO refresh_source_rows (row_id, source_id, resource, watermark, sequence_no, payload_summary) VALUES
    ('row-acme-000001', 'source-acme-erp', 'purchase_orders', '2026-08-25 22:00:00', 1, 'normal batch row'),
    ('row-acme-000002', 'source-acme-erp', 'purchase_orders', '2026-08-25 23:00:00', 2, 'late-arriving row'),
    ('row-beta-000001', 'source-beta-crm', 'accounts', '2026-08-25 22:30:00', 1, 'normal batch row');

INSERT INTO refresh_event_log (event_id, source_id, resource, cursor_value, sequence_no, received_at) VALUES
    ('event-acme-000001', 'source-acme-erp', 'purchase_orders', 'cursor-acme-000001', 1, '2026-08-25 22:05:00'),
    ('event-beta-000001', 'source-beta-crm', 'accounts', 'cursor-beta-000001', 1, '2026-08-25 22:35:00');
"""


def build_docker_compose() -> str:
    return """# Isolated test-only PostgreSQL/MySQL fixtures for the runtime corpus.
# These ports, volumes, and credentials are test infrastructure and are
# never used against production. Always run with a unique -p <project>
# and tear down with `down -v --remove-orphans`.
name: runtime-fixture

networks:
  runtime_fixture:
    driver: bridge

services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: runtime
      POSTGRES_PASSWORD: runtime
      POSTGRES_DB: runtime
    ports:
      - "55432:5432"
    networks:
      - runtime_fixture
    volumes:
      - ./postgres/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
      - ./postgres/seed.sql:/docker-entrypoint-initdb.d/02-seed.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U runtime -d runtime"]
      interval: 5s
      timeout: 5s
      retries: 10

  mysql:
    image: mysql:8
    environment:
      MYSQL_ROOT_PASSWORD: runtime
      MYSQL_USER: runtime
      MYSQL_PASSWORD: runtime
      MYSQL_DATABASE: runtime
    ports:
      - "53306:3306"
    networks:
      - runtime_fixture
    volumes:
      - ./mysql/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
      - ./mysql/seed.sql:/docker-entrypoint-initdb.d/02-seed.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1 -u runtime -pruntime"]
      interval: 5s
      timeout: 5s
      retries: 10
"""


def build_readme(seed: int) -> str:
    return f"""# test_data/runtime/

Generated, deterministic runtime fixture corpus for the Agent Semantic
Infrastructure implementation plan
(`docs/superpowers/plans/2026-08-26-agent-semantic-infrastructure-implementation.md`).
Regenerate with:

```
python test_data/runtime/generate_runtime_fixtures.py --seed {seed} --output <dir>
python test_data/runtime/generate_runtime_fixtures.py --seed {seed} --output <dir> --check
```

`--check` regenerates into a temporary directory and compares the resulting
file hashes against `manifest.json` in `<dir>`, so a stale committed copy
fails loudly instead of silently drifting from the generator.

## Layout

- `manifest.json` -- the case registry: every case's `case_id`, `expected`
  outcome, `coverage` tags, `layers`, `execution_mode`, and exactly one
  `test_targets` descriptor (see `registry.py`).
- `fixtures/{{snapshots,identities,runtime_cases,execution_cases,transport_cases,refresh_cases}}.json`
  -- the synthetic records each case's layer is built from.
- `playwright_seed.json` -- static state (schedule/cursor/config-version/lag,
  cancellation-requested/cancelled/already-terminal, dead-letter/replay/
  configuration-drift) that a later task's Playwright governance specs seed
  the UI from.
- `db/docker-compose.yml`, `db/postgres/*.sql`, `db/mysql/*.sql` -- isolated,
  disposable PostgreSQL 16 / MySQL 8 fixtures (see below). Never production.
- `registry.py` -- `TestTarget`, `targets_for`, `target_is_executable`, and
  `assert_case_registry`: the authoritative case-id-to-test mapping.
- `generate_runtime_fixtures.py` -- this generator (`generate`,
  `validate_manifest`, CLI).
- `test_fixture_manifest.py` -- the generation/registry/no-secret contract
  tests for this corpus.

## Synthetic identifiers

All fixtures use only two synthetic tenants, `tenant-acme` and `tenant-beta`,
synthetic users (`user-<tenant>-operator-<NN>`), and synthetic row/target/
source IDs (e.g. `row-acme-000001`, `target-beta-000001`,
`source-acme-erp`). No fixture contains a real name, email, phone number,
credential, or production hostname; `test_no_pii_or_real_secret` in
`test_fixture_manifest.py` scans every generated byte to enforce this. The
only allowed sentinels are the literal test-only values `runtime/runtime`
(database credentials, isolated to `db/docker-compose.yml`),
`vault:runtime-db`, and the `example.invalid` email domain.

## Isolated database fixtures

`db/docker-compose.yml` publishes PostgreSQL on host port `55432` and MySQL
on host port `53306`, both on the project-scoped `runtime_fixture` network,
both with healthchecks. These ports, the `runtime/runtime` credential, and
the seeded rows are disposable test infrastructure only -- never point them
at a production database. Integration jobs must use a unique Compose
`-p <project>` per run, must refuse to start if a reserved port is already
occupied, and must always tear down with `down -v --remove-orphans`, even on
failure.

Both dialects define the same logical `managed_targets` table (primary key
`target_id`, writable field `status`, optimistic-lock field `row_version`),
plus `refresh_source_rows` and `refresh_event_log`, seeded with stable
source/resource/cursor/sequence rows for `source-acme-erp` and
`source-beta-crm`.

## Simplified cancellation state machine

Refresh runs (`RefreshRun.status`) move through
`queued -> running -> cancel_requested -> cancelled`, or terminate as
`succeeded`, `failed`, or `dead_lettered`. There is **no**
`outcome_committed_at` commit marker anywhere in this contract:

- A `queued` run cancels immediately to terminal `cancelled`.
- A `running` run records `cancel_requested_at`/`cancel_requested_by`/
  `cancel_reason` and moves to `cancel_requested`; the worker transitions it
  to terminal `cancelled` the next time it reaches a **safe point** -- a
  connector page boundary or the instant before tentative DatasetVersion/
  PipelineRun materialization -- with the source cursor and lineage left
  exactly as they were (the no-progress invariant: cancellation never
  advances a cursor and never creates partial lineage).
- If the run's outcome transaction commits *before* a cancellation is
  observed, the run simply finishes `succeeded`/`failed`/`dead_lettered`. A
  cancellation request that arrives after that point makes **no mutation**
  and returns the run with `already_terminal: true` -- this is a plain,
  expected result, never an error and never reported as a successful
  cancellation.

`cancel-before-pull`, `cancel-inflight-page`, and
`cancel-after-tentative-materialization` in `manifest.json` exercise the
three safe points above; each has `cancel_outcome: "cancelled"` and
`cursor_outcome: "unchanged"`.
"""


# ---------------------------------------------------------------------------
# Canonical serialization and manifest assembly
# ---------------------------------------------------------------------------


def _canonical_json(data: dict) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generate(seed: int, output_dir: Path) -> dict:
    """Deterministically generate the full runtime fixture corpus.

    Writes README.md, manifest.json, fixtures/*.json, playwright_seed.json,
    and db/{docker-compose.yml,postgres/*.sql,mysql/*.sql} under
    ``output_dir``, and returns the manifest dict (which includes a
    ``files`` map of relative path -> sha256 of the written bytes).
    """

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "fixtures").mkdir(parents=True, exist_ok=True)
    (output_dir / "db" / "postgres").mkdir(parents=True, exist_ok=True)
    (output_dir / "db" / "mysql").mkdir(parents=True, exist_ok=True)

    case_defs = build_case_defs(seed)
    cases = [_finalize_case(case) for case in case_defs]
    cases.sort(key=lambda c: c["case_id"])

    text_files: dict[str, str] = {
        "README.md": build_readme(seed),
        "db/docker-compose.yml": build_docker_compose(),
        "db/postgres/schema.sql": build_postgres_schema(),
        "db/postgres/seed.sql": build_postgres_seed(),
        "db/mysql/schema.sql": build_mysql_schema(),
        "db/mysql/seed.sql": build_mysql_seed(),
    }
    json_files: dict[str, dict] = {
        **build_fixture_files(cases),
        "playwright_seed.json": build_playwright_seed(),
    }

    files: dict[str, str] = {}
    for rel_path, content in text_files.items():
        data = (content if content.endswith("\n") else content + "\n").encode("utf-8")
        (output_dir / rel_path).write_bytes(data)
        files[rel_path] = _sha256(data)
    for rel_path, content in json_files.items():
        data = _canonical_json(content)
        (output_dir / rel_path).write_bytes(data)
        files[rel_path] = _sha256(data)

    # `files` intentionally excludes manifest.json itself: a manifest cannot
    # durably record its own hash (writing the hash would change the bytes,
    # which would change the hash). validate_manifest() and --check only
    # need to verify the *other* generated files against this map.
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "seed": seed,
        "generated_at": FIXED_NOW,
        "cases": cases,
        "files": files,
    }
    manifest_bytes = _canonical_json(manifest)
    (output_dir / "manifest.json").write_bytes(manifest_bytes)

    # Return exactly what was written to disk.
    return json.loads(manifest_bytes.decode("utf-8"))


def validate_manifest(output_dir: Path) -> None:
    """Verify manifest.json in ``output_dir`` matches the files on disk."""

    output_dir = Path(output_dir)
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == MANIFEST_VERSION
    assert manifest["cases"], "manifest has no cases"

    for case in manifest["cases"]:
        for field in REQUIRED_CASE_FIELDS:
            assert case.get(field), f"case {case.get('case_id')!r} missing required field {field!r}"
        registry.assert_case_registry(case)

    for rel_path, expected_hash in manifest["files"].items():
        actual = _sha256((output_dir / rel_path).read_bytes())
        assert actual == expected_hash, f"{rel_path} does not match manifest hash"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="verify reproducibility against --output")
    args = parser.parse_args(argv)

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = generate(args.seed, Path(tmp))
        existing = json.loads((args.output / "manifest.json").read_text(encoding="utf-8"))
        if fresh["files"] != existing["files"]:
            print("runtime fixture generation is not reproducible: files differ from manifest.json", file=sys.stderr)
            return 1
        validate_manifest(args.output)
        print(f"OK: {args.output} matches a fresh generation with seed {args.seed}")
        return 0

    generate(args.seed, args.output)
    print(f"generated runtime fixture corpus at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
