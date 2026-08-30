"""Unit tests for the governed MySQL row writer (Task 25).

The MySQL dialect twin of `tests/runtime/test_postgres_writer.py` (Task 24).
Every case here is one `MySQLRowWriter.execute` must reject purely from a
`FrozenActionPlan`'s own shape, before it would ever open a database
connection. `pymysql.connect` is monkeypatched to raise if it is ever
called, so each rejection is proven to happen pre-transaction, not just
observed to succeed for some other reason (e.g. an unreachable `mysql_url`).

Cases only detectable once a real write is attempted (row-count mismatches,
a version-column precondition conflict) require a real database and live in
`tests/runtime/integration/test_mysql_writer_integration.py` instead.
"""
from __future__ import annotations

import dataclasses
import traceback

import pymysql
import pytest

from app.services.runtime.writers.base import (
    FrozenActionPlan,
    ManagedRowWriter,
    WriteReceipt,
    WriterError,
)
from app.services.runtime.writers.mysql import MySQLRowWriter

UNREACHABLE_URL = "mysql+pymysql://unused:unused@127.0.0.1:1/unused"


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


# Only the five cases decidable purely from a plan's shape, with no database
# round trip — the other five ("binding-drift", "connection-drift",
# "target-version-conflict", "row-count-zero", "row-count-two") require a
# real row and live in the integration suite.
_UNSAFE_CASES = {
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
    plan = _plan(**_UNSAFE_CASES[case_id])
    MySQLRowWriter(mysql_url).execute(plan, credential_ref="vault:runtime-db")


@pytest.fixture
def mysql_url() -> str:
    return UNREACHABLE_URL


@pytest.mark.parametrize(
    "case_id", ["arbitrary-sql", "ddl", "multi-target", "delete", "wrong-parameter"],
)
def test_mysql_writer_rejects_unsafe_or_stale_plan(mysql_url, case_id, monkeypatch):
    def _fail_if_connected(*args, **kwargs):
        raise AssertionError("MySQLRowWriter must reject this plan before opening a connection")

    monkeypatch.setattr(pymysql, "connect", _fail_if_connected)
    with pytest.raises(WriterError) as exc:
        execute_mysql_fixture(mysql_url, case_id)
    assert exc.value.reason_code in {"BINDING_DRIFT", "PRECONDITION_CONFLICT", "ROW_COUNT_MISMATCH", "UNSUPPORTED_ACTION"}


def test_write_receipt_never_carries_credential_ref_or_a_secret_field():
    field_names = {f.name for f in dataclasses.fields(WriteReceipt)}
    assert "credential_ref" not in field_names
    assert not any("secret" in name for name in field_names)


def test_mysql_row_writer_satisfies_managed_row_writer_protocol():
    assert isinstance(MySQLRowWriter(UNREACHABLE_URL), ManagedRowWriter)


def test_credential_resolution_failure_never_leaks_via_exception_chain(monkeypatch):
    """A resolver's own exception may embed `credential_ref` in its message
    (an ordinary thing for a real vault resolver to do, e.g. `ValueError(f"no
    such secret: {credential_ref}")`). `WriterError` must sever that chain
    (`from None`) so the resolver's original exception — and whatever it
    embedded — can never surface via `__cause__`, `traceback.format_exception`,
    or `logger.exception` of the resulting `WriterError`."""
    marker = "vault:super-secret-ref-marker"

    def _leaky_resolver(credential_ref: str) -> None:
        raise ValueError(f"no such secret: {credential_ref}")

    def _fail_if_connected(*args, **kwargs):
        raise AssertionError("MySQLRowWriter must reject this before opening a connection")

    monkeypatch.setattr(pymysql, "connect", _fail_if_connected)

    writer = MySQLRowWriter(UNREACHABLE_URL, credential_resolver=_leaky_resolver)
    with pytest.raises(WriterError) as exc_info:
        writer.execute(_plan(), credential_ref=marker)

    exc = exc_info.value
    assert exc.__cause__ is None
    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert marker not in str(exc)
    assert marker not in repr(exc)
    assert marker not in tb_text
