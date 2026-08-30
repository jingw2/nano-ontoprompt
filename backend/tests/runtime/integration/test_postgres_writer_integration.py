"""Integration tests for the governed PostgreSQL row writer (Task 24)
against a real, disposable PostgreSQL database
(`test_data/runtime/db/docker-compose.yml`).

Seed-data choice: the runtime fixture's committed `seed.sql` (an earlier
Task 1 fixture-generation pass) ships two `managed_targets` rows
(`target-acme-000001`, `target-beta-000001`), neither named `target-001` as
this task's own test skeleton uses, and modifying `seed.sql` is outside this
task's Files list. Every test below therefore inserts and cleans up its own
disposable `managed_targets` row(s) directly, in the test's own setup/
teardown — a normal, self-contained integration-test pattern — and never
depends on or mutates the two pre-seeded rows.

Run: docker compose -f test_data/runtime/db/docker-compose.yml up -d --wait postgres
     RUNTIME_POSTGRES_URL=postgresql://runtime:runtime@localhost:55432/runtime \
         python -m pytest tests/runtime/integration/test_postgres_writer_integration.py -q
"""
from __future__ import annotations

import dataclasses
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from app.services.runtime.writers.base import FrozenActionPlan, WriteReceipt, WriterError
from app.services.runtime.writers.postgres import PostgresRowWriter

RUNTIME_POSTGRES_URL = os.environ.get("RUNTIME_POSTGRES_URL")


@pytest.fixture
def postgres_url() -> str:
    if not RUNTIME_POSTGRES_URL:
        pytest.skip("RUNTIME_POSTGRES_URL is not configured")
    return RUNTIME_POSTGRES_URL


def read_target_status(postgres_url: str, target_id: str) -> str:
    connection = psycopg2.connect(postgres_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT status FROM managed_targets WHERE target_id = %s", (target_id,))
            row = cursor.fetchone()
            assert row is not None, f"no managed_targets row for {target_id!r}"
            return row[0]
    finally:
        connection.close()


@contextmanager
def _seeded_row(postgres_url: str, *, target_id: str, tenant_id: str, status: str = "active", row_version: int = 1):
    """Insert one disposable `managed_targets` row for the duration of the
    `with` block, then delete it — never touching the two shared seed rows."""
    connection = psycopg2.connect(postgres_url)
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
        cleanup = psycopg2.connect(postgres_url)
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
        connection_target_identity="postgres://runtime-db#fingerprint",
        dialect="postgresql",
        schema_name="public",
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
def auto_frozen_plan(postgres_url):
    with _seeded_row(postgres_url, target_id="target-001", tenant_id="tenant-writer-001"):
        yield _plan()


def test_postgres_writer_updates_exactly_one_frozen_row(postgres_url, auto_frozen_plan):
    receipt = PostgresRowWriter(postgres_url).execute(auto_frozen_plan, credential_ref="vault:runtime-db")
    assert receipt.affected_rows == 1
    assert read_target_status(postgres_url, "target-001") == "approved"
    assert receipt.idempotency_key == auto_frozen_plan.idempotency_key
    assert receipt.dialect == "postgresql"
    assert receipt.status == "committed"
    assert receipt.primary_key_tuple == auto_frozen_plan.primary_key_tuple


# The five cases decidable purely from a plan's own shape (mirrors
# tests/runtime/test_postgres_writer.py) plus the three only detectable once
# the actual write is attempted, exactly as the brief's own skeleton
# parametrizes them together.
_SHAPE_ONLY_CASES = {
    "arbitrary-sql": dict(table_name='managed_targets"; DROP TABLE managed_targets; --'),
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


def execute_postgres_fixture(postgres_url: str, case_id: str) -> None:
    if case_id in _SHAPE_ONLY_CASES:
        plan = _plan(**_SHAPE_ONLY_CASES[case_id])
        PostgresRowWriter(postgres_url).execute(plan, credential_ref="vault:runtime-db")
        return

    if case_id == "target-version-conflict":
        with _seeded_row(postgres_url, target_id="target-conflict-001", tenant_id="tenant-writer-002"):
            plan = _plan(
                primary_key_tuple=(("target_id", "target-conflict-001"),),
                expected_version=999,  # the live row's row_version is 1
            )
            PostgresRowWriter(postgres_url).execute(plan, credential_ref="vault:runtime-db")
        return

    if case_id == "row-count-zero":
        plan = _plan(primary_key_tuple=(("target_id", "target-does-not-exist-xyz"),))
        PostgresRowWriter(postgres_url).execute(plan, credential_ref="vault:runtime-db")
        return

    if case_id == "row-count-two":
        shared_tenant = f"tenant-shared-{uuid.uuid4().hex[:8]}"
        target_a = f"target-dup-a-{uuid.uuid4().hex[:8]}"
        target_b = f"target-dup-b-{uuid.uuid4().hex[:8]}"
        connection = psycopg2.connect(postgres_url)
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
            PostgresRowWriter(postgres_url).execute(plan, credential_ref="vault:runtime-db")
        finally:
            cleanup = psycopg2.connect(postgres_url)
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
    "arbitrary-sql", "ddl", "multi-target", "delete", "wrong-parameter",
    "target-version-conflict", "row-count-zero", "row-count-two",
])
def test_postgres_writer_rejects_unsafe_or_stale_plan(postgres_url, case_id):
    with pytest.raises(WriterError) as exc:
        execute_postgres_fixture(postgres_url, case_id)
    assert exc.value.reason_code in {"UNSUPPORTED_ACTION", "PRECONDITION_CONFLICT", "ROW_COUNT_MISMATCH"}


def test_postgres_writer_rolls_back_on_statement_timeout(postgres_url):
    """A concurrent holder of the same row's lock forces the writer's own
    short `SET LOCAL statement_timeout` to fire; the write must roll back
    entirely rather than leave the row half-touched."""
    with _seeded_row(postgres_url, target_id="target-timeout-001", tenant_id="tenant-writer-timeout"):
        blocker = psycopg2.connect(postgres_url)
        blocker.autocommit = False
        try:
            with blocker.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM managed_targets WHERE target_id = %s FOR UPDATE", ("target-timeout-001",),
                )
            writer = PostgresRowWriter(postgres_url, statement_timeout_ms=200)
            plan = _plan(primary_key_tuple=(("target_id", "target-timeout-001"),))
            with pytest.raises(WriterError) as exc:
                writer.execute(plan, credential_ref="vault:runtime-db")
            assert exc.value.reason_code == "PRECONDITION_CONFLICT"
        finally:
            blocker.rollback()
            blocker.close()
        assert read_target_status(postgres_url, "target-timeout-001") == "active"


def test_postgres_writer_stores_sql_injection_payload_as_inert_value(postgres_url):
    """The 8 cases in `test_postgres_writer_rejects_unsafe_or_stale_plan`
    above all attack structure (bad identifiers, wrong column counts,
    disallowed parameter keys) — none attempt a legitimate-shaped write
    whose VALUE contains SQL metacharacters. This proves the positive case:
    a plan that is otherwise entirely valid, but whose parameter value is a
    SQL injection payload, must be stored as an inert literal string — never
    executed as SQL — because every value is bound as a query parameter,
    never string-interpolated."""
    # 31 chars: fits the schema's `status VARCHAR(32)` column while still
    # being a genuine stacked-query DROP-TABLE injection attempt.
    payload = "x'; DROP TABLE managed_targets;"

    def _row_count() -> int:
        connection = psycopg2.connect(postgres_url)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM managed_targets")
                return cursor.fetchone()[0]
        finally:
            connection.close()

    baseline_count = _row_count()
    with _seeded_row(postgres_url, target_id="target-injection-001", tenant_id="tenant-writer-injection"):
        plan = _plan(
            primary_key_tuple=(("target_id", "target-injection-001"),),
            parameters={"status": payload},
        )
        receipt = PostgresRowWriter(postgres_url).execute(plan, credential_ref="vault:runtime-db")
        assert receipt.affected_rows == 1
        assert read_target_status(postgres_url, "target-injection-001") == payload
        # The payload was stored as inert data, not executed as SQL: the
        # table still exists and its row count only grew by our one insert.
        assert _row_count() == baseline_count + 1
    assert _row_count() == baseline_count


def test_write_receipt_never_discloses_credential_ref_or_secret(postgres_url, auto_frozen_plan):
    receipt = PostgresRowWriter(postgres_url).execute(auto_frozen_plan, credential_ref="vault:super-secret-ref")
    field_names = {f.name for f in dataclasses.fields(WriteReceipt)}
    assert "credential_ref" not in field_names
    assert not any("secret" in name for name in field_names)
    assert "vault:super-secret-ref" not in repr(receipt)
    assert "vault:super-secret-ref" not in str(dataclasses.asdict(receipt))
