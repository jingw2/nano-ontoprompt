"""Unit tests for the governed PostgreSQL row writer (Task 24).

Every case here is one `PostgresRowWriter.execute` must reject purely from a
`FrozenActionPlan`'s own shape, before it would ever open a database
connection — the brief's own text: "Reject SQL, identifier, connection,
transaction-option, target-selector, delete, DDL, and multi-target inputs
before opening a transaction." `psycopg2.connect` is monkeypatched to raise
if it is ever called, so each rejection is proven to happen pre-transaction,
not just observed to succeed for some other reason (e.g. an unreachable
`postgres_url`).

Cases only detectable once a real write is attempted (row-count mismatches,
a version-column precondition conflict) require a real database and live in
`tests/runtime/integration/test_postgres_writer_integration.py` instead.
"""
from __future__ import annotations

import dataclasses

import psycopg2
import pytest

from app.services.runtime.writers.base import (
    FrozenActionPlan,
    ManagedRowWriter,
    WriteReceipt,
    WriterError,
)
from app.services.runtime.writers.postgres import PostgresRowWriter

UNREACHABLE_URL = "postgresql://unused:unused@127.0.0.1:1/unused"


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


# Only the five cases decidable purely from a plan's shape, with no database
# round trip — the other three ("target-version-conflict", "row-count-zero",
# "row-count-two") require a real row and live in the integration suite.
_UNSAFE_CASES = {
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
    plan = _plan(**_UNSAFE_CASES[case_id])
    PostgresRowWriter(postgres_url).execute(plan, credential_ref="vault:runtime-db")


@pytest.fixture
def postgres_url() -> str:
    return UNREACHABLE_URL


@pytest.mark.parametrize(
    "case_id", ["arbitrary-sql", "ddl", "multi-target", "delete", "wrong-parameter"],
)
def test_postgres_writer_rejects_unsafe_or_stale_plan(postgres_url, case_id, monkeypatch):
    def _fail_if_connected(*args, **kwargs):
        raise AssertionError("PostgresRowWriter must reject this plan before opening a connection")

    monkeypatch.setattr(psycopg2, "connect", _fail_if_connected)
    with pytest.raises(WriterError) as exc:
        execute_postgres_fixture(postgres_url, case_id)
    assert exc.value.reason_code in {"UNSUPPORTED_ACTION", "PRECONDITION_CONFLICT", "ROW_COUNT_MISMATCH"}


def test_write_receipt_never_carries_credential_ref_or_a_secret_field():
    field_names = {f.name for f in dataclasses.fields(WriteReceipt)}
    assert "credential_ref" not in field_names
    assert not any("secret" in name for name in field_names)


def test_postgres_row_writer_satisfies_managed_row_writer_protocol():
    assert isinstance(PostgresRowWriter(UNREACHABLE_URL), ManagedRowWriter)
