"""Integration tests for the governed MySQL row writer (Task 25) against a
real, disposable MySQL database (`test_data/runtime/db/docker-compose.yml`).

Seed-data choice: mirrors `test_postgres_writer_integration.py`'s own
rationale exactly. The runtime fixture's committed `mysql/seed.sql` ships two
`managed_targets` rows (`target-acme-000001`, `target-beta-000001`), neither
named `target-001` as this task's own test skeleton uses, and modifying
`seed.sql` is outside this task's Files list. Every test below therefore
inserts and cleans up its own disposable `managed_targets` row(s) directly,
in the test's own setup/teardown, and never depends on or mutates the two
pre-seeded rows.

`binding-drift`/`connection-drift` note: Task 21's `resolve_published_binding`
(a separate, earlier check that happens before a `FrozenActionPlan` is even
constructed) is what would normally detect a stale binding or connection —
the writer itself has no live binding/connection to check against, only the
already-frozen plan, and must never re-fetch upstream state to look for one
(this module family's boundary since Task 21). Both cases are therefore
modeled here as scenarios where the frozen plan itself reflects a
stale/inconsistent live-database state, exactly like `target-version-conflict`
and `row-count-zero` already do for PostgreSQL: `binding-drift` reuses
`target-version-conflict`'s mechanism (an `expected_version` precondition
that no longer matches the live row — as if the binding was re-frozen after
the row changed underneath it); `connection-drift` reuses `row-count-zero`'s
mechanism (the target row is no longer there at all — as if the connection's
underlying dataset drifted since the plan was frozen). Neither adds any new
drift-detection capability to the writer; both fall through to the same
`ROW_COUNT_MISMATCH` path the equivalent PostgreSQL stale-state cases use.

Run: docker compose -f test_data/runtime/db/docker-compose.yml up -d --wait mysql
     RUNTIME_MYSQL_URL=mysql+pymysql://runtime:runtime@localhost:53306/runtime \
         python -m pytest tests/runtime/integration/test_mysql_writer_integration.py -q
"""
from __future__ import annotations

import dataclasses
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

pymysql = pytest.importorskip("pymysql")

from app.services.runtime.writers.base import FrozenActionPlan, WriteReceipt, WriterError
from app.services.runtime.writers.mysql import MySQLRowWriter, _parse_mysql_url

RUNTIME_MYSQL_URL = os.environ.get("RUNTIME_MYSQL_URL")


@pytest.fixture
def mysql_url() -> str:
    if not RUNTIME_MYSQL_URL:
        pytest.skip("RUNTIME_MYSQL_URL is not configured")
    return RUNTIME_MYSQL_URL


def _connect(mysql_url: str):
    return pymysql.connect(**_parse_mysql_url(mysql_url), autocommit=False)


def read_target_status(mysql_url: str, target_id: str) -> str:
    connection = _connect(mysql_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT status FROM managed_targets WHERE target_id = %s", (target_id,))
            row = cursor.fetchone()
            assert row is not None, f"no managed_targets row for {target_id!r}"
            return row[0]
    finally:
        connection.close()


@contextmanager
def _seeded_row(mysql_url: str, *, target_id: str, tenant_id: str, status: str = "active", row_version: int = 1):
    """Insert one disposable `managed_targets` row for the duration of the
    `with` block, then delete it — never touching the two shared seed rows."""
    connection = _connect(mysql_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO managed_targets (target_id, tenant_id, status, row_version, updated_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (target_id, tenant_id, status, row_version, datetime.now(timezone.utc)),
            )
        connection.commit()
    finally:
        connection.close()
    try:
        yield
    finally:
        cleanup = _connect(mysql_url)
        try:
            with cleanup.cursor() as cursor:
                cursor.execute("DELETE FROM managed_targets WHERE target_id = %s", (target_id,))
            cleanup.commit()
        finally:
            cleanup.close()


def _plan(**overrides) -> FrozenActionPlan:
    defaults: dict = dict(
        managed_action_binding_id="binding-001",
        binding_version=1,
        connection_target_identity="mysql://runtime-db#fingerprint",
        dialect="mysql",
        schema_name="runtime",
        table_name="managed_targets",
        primary_key_columns=("target_id",),
        writable_columns=("status",),
        version_column="row_version",
        primary_key_tuple=(("target_id", "target-001"),),
        parameters={"status": "approved"},
        expected_version=1,
        before_image_hash="b" * 64,
        version_hash="v" * 64,
        idempotency_key="idem-001",
    )
    defaults.update(overrides)
    return FrozenActionPlan(**defaults)


@pytest.fixture
def auto_frozen_plan(mysql_url):
    with _seeded_row(mysql_url, target_id="target-001", tenant_id="tenant-writer-001"):
        yield _plan()


def test_mysql_writer_updates_exactly_one_frozen_row(mysql_url, auto_frozen_plan):
    receipt = MySQLRowWriter(mysql_url).execute(auto_frozen_plan, credential_ref="vault:runtime-db")
    assert receipt.affected_rows == 1
    assert read_target_status(mysql_url, "target-001") == "approved"
    assert receipt.idempotency_key == auto_frozen_plan.idempotency_key
    assert receipt.dialect == "mysql"
    assert receipt.status == "committed"
    assert receipt.primary_key_tuple == auto_frozen_plan.primary_key_tuple


# The five cases decidable purely from a plan's own shape (mirrors
# tests/runtime/test_mysql_writer.py) plus the five only detectable once the
# actual write is attempted, exactly as the brief's own skeleton
# parametrizes them together.
_SHAPE_ONLY_CASES = {
    "arbitrary-sql": dict(table_name="managed_targets`; DROP TABLE managed_targets; --"),
    "ddl": dict(
        writable_columns=("status", "status; ALTER TABLE managed_targets DROP COLUMN status; --"),
        parameters={
            "status": "approved",
            "status; ALTER TABLE managed_targets DROP COLUMN status; --": "x",
        },
    ),
    "multi-target": dict(
        primary_key_tuple=(("target_id", "target-001"), ("target_id", "target-002")),
    ),
    "delete": dict(parameters={}),
    "wrong-parameter": dict(parameters={"tenant_id": "hacked-tenant"}),
}


def execute_mysql_fixture(mysql_url: str, case_id: str) -> None:
    if case_id in _SHAPE_ONLY_CASES:
        plan = _plan(**_SHAPE_ONLY_CASES[case_id])
        MySQLRowWriter(mysql_url).execute(plan, credential_ref="vault:runtime-db")
        return

    if case_id in ("target-version-conflict", "binding-drift"):
        target_id = f"target-conflict-{uuid.uuid4().hex[:8]}"
        with _seeded_row(mysql_url, target_id=target_id, tenant_id="tenant-writer-002"):
            plan = _plan(
                primary_key_tuple=(("target_id", target_id),),
                expected_version=999,  # the live row's row_version is 1
            )
            MySQLRowWriter(mysql_url).execute(plan, credential_ref="vault:runtime-db")
        return

    if case_id in ("row-count-zero", "connection-drift"):
        plan = _plan(primary_key_tuple=(("target_id", f"target-does-not-exist-{uuid.uuid4().hex[:8]}"),))
        MySQLRowWriter(mysql_url).execute(plan, credential_ref="vault:runtime-db")
        return

    if case_id == "row-count-two":
        shared_tenant = f"tenant-shared-{uuid.uuid4().hex[:8]}"
        target_a = f"target-dup-a-{uuid.uuid4().hex[:8]}"
        target_b = f"target-dup-b-{uuid.uuid4().hex[:8]}"
        connection = _connect(mysql_url)
        try:
            with connection.cursor() as cursor:
                for target_id in (target_a, target_b):
                    cursor.execute(
                        "INSERT INTO managed_targets (target_id, tenant_id, status, row_version, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (target_id, shared_tenant, "active", 1, datetime.now(timezone.utc)),
                    )
            connection.commit()
        finally:
            connection.close()
        try:
            # A misconfigured/adversarial binding whose declared "primary
            # key" (tenant_id) is not actually unique at the database level.
            # The writer must never trust that a binding's own primary key
            # declaration is unique — it must independently require exactly
            # one affected row and roll back otherwise.
            plan = _plan(
                primary_key_columns=("tenant_id",),
                primary_key_tuple=(("tenant_id", shared_tenant),),
            )
            MySQLRowWriter(mysql_url).execute(plan, credential_ref="vault:runtime-db")
        finally:
            cleanup = _connect(mysql_url)
            try:
                with cleanup.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM managed_targets WHERE target_id IN (%s, %s)", (target_a, target_b),
                    )
                cleanup.commit()
            finally:
                cleanup.close()
        return

    raise AssertionError(f"unknown case_id: {case_id!r}")


@pytest.mark.parametrize("case_id", [
    "binding-drift", "connection-drift", "wrong-parameter",
    "target-version-conflict", "row-count-zero", "row-count-two",
    "arbitrary-sql", "ddl", "multi-target", "delete",
])
def test_mysql_writer_rejects_unsafe_or_stale_plan(mysql_url, case_id):
    with pytest.raises(WriterError) as exc:
        execute_mysql_fixture(mysql_url, case_id)
    assert exc.value.reason_code in {"BINDING_DRIFT", "PRECONDITION_CONFLICT", "ROW_COUNT_MISMATCH", "UNSUPPORTED_ACTION"}


def test_mysql_writer_rolls_back_on_lock_wait_timeout(mysql_url):
    """A concurrent holder of the same row's lock forces the writer's own
    short `SET SESSION innodb_lock_wait_timeout` to fire; the write must roll
    back entirely rather than leave the row half-touched. This is MySQL's own
    analog of `test_postgres_writer_rolls_back_on_statement_timeout`: MySQL
    has no per-statement DML timeout (`MAX_EXECUTION_TIME` only bounds
    SELECT), so `innodb_lock_wait_timeout` — the real mechanism that bounds
    how long a row-lock wait may block — is what actually reproduces "a
    transaction that would otherwise hang forever gets bounded and rolled
    back instead" on MySQL/InnoDB."""
    with _seeded_row(mysql_url, target_id="target-timeout-001", tenant_id="tenant-writer-timeout"):
        blocker = _connect(mysql_url)
        try:
            with blocker.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM managed_targets WHERE target_id = %s FOR UPDATE", ("target-timeout-001",),
                )
            writer = MySQLRowWriter(mysql_url, lock_wait_timeout_seconds=1)
            plan = _plan(primary_key_tuple=(("target_id", "target-timeout-001"),))
            with pytest.raises(WriterError) as exc:
                writer.execute(plan, credential_ref="vault:runtime-db")
            assert exc.value.reason_code == "PRECONDITION_CONFLICT"
        finally:
            blocker.rollback()
            blocker.close()
        assert read_target_status(mysql_url, "target-timeout-001") == "active"


def test_mysql_writer_stores_sql_injection_payload_as_inert_value(mysql_url):
    """The 10 cases in `test_mysql_writer_rejects_unsafe_or_stale_plan` above
    all attack structure (bad identifiers, wrong column counts, disallowed
    parameter keys, stale versions/rows) — none attempt a legitimate-shaped
    write whose VALUE contains SQL metacharacters. This proves the positive
    case: a plan that is otherwise entirely valid, but whose parameter value
    is a SQL injection payload, must be stored as an inert literal string —
    never executed as SQL — because every value is bound as a query
    parameter, never string-interpolated."""
    # 31 chars: fits the schema's `status VARCHAR(32)` column while still
    # being a genuine stacked-query DROP-TABLE injection attempt.
    payload = "x'; DROP TABLE managed_targets;"

    def _row_count() -> int:
        connection = _connect(mysql_url)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM managed_targets")
                return cursor.fetchone()[0]
        finally:
            connection.close()

    baseline_count = _row_count()
    with _seeded_row(mysql_url, target_id="target-injection-001", tenant_id="tenant-writer-injection"):
        plan = _plan(
            primary_key_tuple=(("target_id", "target-injection-001"),),
            parameters={"status": payload},
        )
        receipt = MySQLRowWriter(mysql_url).execute(plan, credential_ref="vault:runtime-db")
        assert receipt.affected_rows == 1
        assert read_target_status(mysql_url, "target-injection-001") == payload
        # The payload was stored as inert data, not executed as SQL: the
        # table still exists and its row count only grew by our one insert.
        assert _row_count() == baseline_count + 1
    assert _row_count() == baseline_count


def test_write_receipt_never_discloses_credential_ref_or_secret(mysql_url, auto_frozen_plan):
    receipt = MySQLRowWriter(mysql_url).execute(auto_frozen_plan, credential_ref="vault:super-secret-ref")
    field_names = {f.name for f in dataclasses.fields(WriteReceipt)}
    assert "credential_ref" not in field_names
    assert not any("secret" in name for name in field_names)
    assert "vault:super-secret-ref" not in repr(receipt)
    assert "vault:super-secret-ref" not in str(dataclasses.asdict(receipt))
