"""Cross-dialect parity tests for the governed row writer (Tasks 24/25),
against real, disposable PostgreSQL and MySQL databases
(`test_data/runtime/db/docker-compose.yml`).

`tests/runtime/integration/test_postgres_writer_integration.py` and
`tests/runtime/integration/test_mysql_writer_integration.py` already prove
every individual per-dialect governance boundary (their own case_ids include
an equivalent `target-version-conflict`/`row-count-*` scenario each), but as
two independently-parametrized suites, one dialect's node could regress
without the other ever failing. This suite is the cross-dialect complement:
it drives `PostgresRowWriter` and `MySQLRowWriter` against an identically-
shaped scenario inside ONE test each, so both real writers are proven to
behave identically for the same governed input.

Run:
    docker compose -f test_data/runtime/db/docker-compose.yml up -d --wait
    RUNTIME_POSTGRES_URL=postgresql://runtime:runtime@localhost:55432/runtime \\
        RUNTIME_MYSQL_URL=mysql+pymysql://runtime:runtime@localhost:53306/runtime \\
        python -m pytest tests/runtime/integration/test_managed_row_writer_dialects.py -q

Each dialect's half of a test skips (rather than fails) when its own URL env
var is not configured, exactly like its sibling per-dialect integration
suites.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from app.services.runtime.writers.base import FrozenActionPlan, WriterError
from app.services.runtime.writers.mysql import MySQLRowWriter, _parse_mysql_url
from app.services.runtime.writers.postgres import PostgresRowWriter

_DIALECT_URL_ENV = {"postgresql": "RUNTIME_POSTGRES_URL", "mysql": "RUNTIME_MYSQL_URL"}
_DIALECT_SCHEMA = {"postgresql": "public", "mysql": "runtime"}
_WRITER_CLS = {"postgresql": PostgresRowWriter, "mysql": MySQLRowWriter}


def _require_dialect_url(dialect: str) -> str:
    url = os.environ.get(_DIALECT_URL_ENV[dialect])
    if not url:
        pytest.skip(f"{_DIALECT_URL_ENV[dialect]} is not configured")
    return url


def _connect(dialect: str, url: str):
    if dialect == "postgresql":
        psycopg2 = pytest.importorskip("psycopg2")
        return psycopg2.connect(url)
    pymysql = pytest.importorskip("pymysql")
    return pymysql.connect(**_parse_mysql_url(url), autocommit=False)


@contextmanager
def _seeded_row(dialect: str, url: str, *, target_id: str, tenant_id: str,
                 status: str = "active", row_version: int = 1):
    """Insert one disposable `managed_targets` row for the duration of the
    `with` block, then delete it — never touching either dialect's shared
    seed rows, mirroring the sibling per-dialect integration suites."""
    connection = _connect(dialect, url)
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
        cleanup = _connect(dialect, url)
        try:
            with cleanup.cursor() as cursor:
                cursor.execute("DELETE FROM managed_targets WHERE target_id = %s", (target_id,))
            cleanup.commit()
        finally:
            cleanup.close()


def _read_row(dialect: str, url: str, target_id: str):
    connection = _connect(dialect, url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, row_version FROM managed_targets WHERE target_id = %s", (target_id,),
            )
            return cursor.fetchone()
    finally:
        connection.close()


def _plan(dialect: str, **overrides) -> FrozenActionPlan:
    defaults: dict = dict(
        managed_action_binding_id="binding-parity-001",
        binding_version=1,
        connection_target_identity=f"{dialect}://runtime-db#fingerprint",
        dialect=dialect,
        schema_name=_DIALECT_SCHEMA[dialect],
        table_name="managed_targets",
        primary_key_columns=("target_id",),
        writable_columns=("status",),
        version_column="row_version",
        primary_key_tuple=(("target_id", "target-parity-001"),),
        parameters={"status": "approved"},
        expected_version=1,
        before_image_hash="b" * 64,
        version_hash="v" * 64,
        idempotency_key=f"idem-{uuid.uuid4()}",
    )
    defaults.update(overrides)
    return FrozenActionPlan(**defaults)


def test_identical_rows_and_versions_across_dialects():
    """`database-row-parity`: a genuine single-row governed write must
    produce the identical optimistic-lock outcome — exactly one affected
    row, gated on the real live `row_version` it was frozen against, the new
    column value actually persisted, AND `row_version` itself genuinely
    advanced by the write — whichever real dialect backs `managed_targets`.

    `PostgresRowWriter`/`MySQLRowWriter` both gate the write on `row_version`
    (via the WHERE clause) AND bump it (via `SET ... row_version =
    row_version + 1` in the same statement) — this closes a lost-update race
    where two different writers could otherwise both observe the same live
    `row_version`, both pass the precondition, and the second write would
    silently clobber the first with no detected conflict. See
    `test_second_write_against_stale_original_version_is_rejected` below for
    a test that proves that race is actually closed.
    """
    for dialect in ("postgresql", "mysql"):
        url = _require_dialect_url(dialect)
        target_id = f"target-parity-{uuid.uuid4().hex[:8]}"
        with _seeded_row(dialect, url, target_id=target_id, tenant_id="tenant-parity", row_version=1):
            plan = _plan(dialect, primary_key_tuple=(("target_id", target_id),), expected_version=1)
            receipt = _WRITER_CLS[dialect](url).execute(plan, credential_ref="vault:runtime-db")
            assert receipt.affected_rows == 1
            assert receipt.status == "committed"

            row = _read_row(dialect, url, target_id)
            assert row is not None
            assert row[0] == "approved"
            assert row[1] == 2, f"{dialect}: row_version must be incremented by a successful write"


def test_second_write_against_stale_original_version_is_rejected():
    """`database-row-version-race-closed`: two different governed writes
    (e.g. from two different `RuntimePlan`s/agents) that both read the same
    live `row_version` before either commits must never both succeed — the
    second one must be rejected as a real optimistic-lock conflict, and the
    row must reflect only the first write's value.

    This reproduces the exact lost-update race the version-increment fix
    closes: seed `row_version=1`, execute one write successfully (which must
    bump the live version to `2`), then attempt a second write against the
    SAME row using the ORIGINAL `expected_version=1` — simulating a second
    writer that read the version before the first one committed. Before the
    fix (writer gates on but never bumps `version_column`), this second
    write would incorrectly succeed, silently overwriting the first write's
    result with zero detected conflict.
    """
    for dialect in ("postgresql", "mysql"):
        url = _require_dialect_url(dialect)
        target_id = f"target-race-{uuid.uuid4().hex[:8]}"
        with _seeded_row(dialect, url, target_id=target_id, tenant_id="tenant-race", row_version=1):
            first_plan = _plan(
                dialect, primary_key_tuple=(("target_id", target_id),),
                expected_version=1, parameters={"status": "approved"},
            )
            first_receipt = _WRITER_CLS[dialect](url).execute(first_plan, credential_ref="vault:runtime-db")
            assert first_receipt.affected_rows == 1

            row_after_first = _read_row(dialect, url, target_id)
            assert row_after_first == ("approved", 2), f"{dialect}: first write must succeed and bump the version"

            # Second writer read `row_version=1` before the first writer's
            # commit landed, so it still presents the now-stale
            # `expected_version=1`.
            second_plan = _plan(
                dialect, primary_key_tuple=(("target_id", target_id),),
                expected_version=1, parameters={"status": "rejected"},
            )
            with pytest.raises(WriterError) as exc:
                _WRITER_CLS[dialect](url).execute(second_plan, credential_ref="vault:runtime-db")
            assert exc.value.reason_code == "ROW_COUNT_MISMATCH"

            row_after_second = _read_row(dialect, url, target_id)
            assert row_after_second == ("approved", 2), (
                f"{dialect}: row must still reflect only the first write after the second is rejected"
            )


def test_target_row_changes_between_plan_and_execution():
    """`database-target-row-drift`: if the live target row's version no
    longer matches what the frozen plan/binding captured (e.g. another
    writer already moved it since the plan was proposed), the write must be
    rejected as a real optimistic-lock failure and the row left untouched —
    never silently applied against the wrong row generation — for both
    dialects."""
    for dialect in ("postgresql", "mysql"):
        url = _require_dialect_url(dialect)
        target_id = f"target-drift-{uuid.uuid4().hex[:8]}"
        with _seeded_row(dialect, url, target_id=target_id, tenant_id="tenant-drift", row_version=1):
            plan = _plan(
                dialect, primary_key_tuple=(("target_id", target_id),),
                expected_version=999,  # the live row's row_version is still 1
            )
            with pytest.raises(WriterError) as exc:
                _WRITER_CLS[dialect](url).execute(plan, credential_ref="vault:runtime-db")
            assert exc.value.reason_code == "ROW_COUNT_MISMATCH"

            row = _read_row(dialect, url, target_id)
            assert row == ("active", 1), f"{dialect}: row must be untouched after a rejected write"
