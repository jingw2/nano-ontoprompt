"""Live-dialect behavioral coverage for the lowercase-hex CHECK constraints
added by 0027/0029/0030/0032/0033/0034: each enforces its id/hash column is
lowercase hex, and MySQL's default `utf8mb4_0900_ai_ci` collation makes a
plain `REGEXP` case-insensitive -- `REGEXP_LIKE(..., 'c')` is required to
actually reject an uppercase value.

`tests/agent/test_runtime_migration_dialects.py`'s
`test_runtime_migrations_render_with_mysql_dialect` only asserts `"REGEXP"`
is a substring of the statically rendered SQL for 0029/0030; it never runs
against a real MySQL server, covers only 2 of the 6 affected migrations,
and cannot prove an uppercase value is actually rejected (the exact gap a
plain `REGEXP` without the `'c'` flag would not have failed either). This
module closes that gap: it applies each migration's own constraint-text
helper against a live database and inserts real rows.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, Column, MetaData, String, Table, create_engine, insert
from sqlalchemy.exc import DBAPIError

BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_DIR = BACKEND_DIR / "alembic" / "versions"


def _load_migration(revision: str):
    path = MIGRATION_DIR / f"{revision}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# One case per migration whose CHECK constraint enforces lowercase-hex UUID
# via the shared `_uuid_check(column)` helper each of these five migrations
# defines locally (real UUID_PATTERN, real per-dialect branch -- this test
# calls that helper directly rather than duplicating its regex).
_UUID_REVISIONS = [
    "0029_runtime_identity",
    "0030_runtime_plans",
    "0032_managed_action_bindings",
    "0033_sandbox",
    "0034_runtime_execution",
]
_VALID_UUID = "11111111-1111-1111-1111-111111111111"
_INVALID_UUID = "AAAAAAAA-BBBB-CCCC-DDDD-111111111111"

_VALID_HASH = "a" * 64
_INVALID_HASH = "A" * 64


class _CheckConstraintCapture:
    """A fake `op` that runs 0027's real `upgrade()` far enough to capture
    the literal condition string it passes to `create_check_constraint` for
    `ck_semantic_snapshots_materialization_hash_hex`, instead of hand-
    duplicating that regex in this test (0027 has no reusable
    `_uuid_check`-style helper to call directly, unlike the five UUID
    migrations above). Every other `op.*` call `upgrade()` makes -- table/
    index creation, raw trigger SQL -- is accepted and discarded; only the
    dialect-conditional `create_check_constraint` branch that actually
    fires for `dialect_name` is captured."""

    def __init__(self, dialect_name: str):
        self._dialect_name = dialect_name
        self.captured: dict[str, str] = {}

    def get_bind(self):
        return self

    @property
    def dialect(self):
        return self

    @property
    def name(self):
        return self._dialect_name

    def create_check_constraint(self, name, table_name, condition, **kwargs):
        self.captured[name] = condition

    def __getattr__(self, item):
        def _noop(*args, **kwargs):
            return None
        return _noop


def _materialization_hash_expr(sql_dialect: str) -> str:
    migration = _load_migration("0027_semantic_snapshot")
    capture = _CheckConstraintCapture(sql_dialect)
    migration.op = capture
    migration.upgrade()
    return capture.captured["ck_semantic_snapshots_materialization_hash_hex"]


def _assert_check_constraint_accepts_lower_rejects_upper(
    engine, dialect: str, expr: str, table_name: str, valid_value: str, invalid_value: str,
) -> None:
    metadata = MetaData()
    table = Table(
        table_name, metadata,
        Column("val", String(64), nullable=False),
        CheckConstraint(expr, name=f"ck_{table_name}"),
    )
    with engine.begin() as connection:
        table.drop(bind=connection, checkfirst=True)
        table.create(bind=connection)
    try:
        # Each statement runs in its own transaction so a MySQL/Postgres
        # constraint violation on the invalid insert cannot poison a
        # shared transaction the rest of the test still needs.
        with engine.begin() as connection:
            connection.execute(insert(table).values(val=valid_value))
        # Postgres raises IntegrityError for a CHECK violation; MySQL's
        # pymysql driver raises OperationalError (error 3819) for the same
        # violation -- DBAPIError is the common base for both.
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(insert(table).values(val=invalid_value))
    finally:
        with engine.begin() as connection:
            table.drop(bind=connection, checkfirst=True)


@pytest.mark.parametrize(
    ("dialect", "env_name"),
    [("postgres", "RUNTIME_POSTGRES_URL"), ("mysql", "RUNTIME_MYSQL_URL")],
)
@pytest.mark.parametrize("revision", _UUID_REVISIONS)
def test_migration_uuid_check_constraint_rejects_uppercase_hex(dialect, env_name, revision):
    """Real MySQL/Postgres proof, per migration: a lowercase UUID is
    accepted and an uppercase-hex UUID is rejected by the exact CHECK
    expression that migration's own `_uuid_check` helper produces."""
    url = os.environ.get(env_name)
    if not url:
        pytest.skip(f"{env_name} is not configured")
    pytest.importorskip("psycopg2" if dialect == "postgres" else "pymysql")

    migration = _load_migration(revision)
    sql_dialect = "postgresql" if dialect == "postgres" else "mysql"
    migration._dialect_name = lambda: sql_dialect
    expr = migration._uuid_check("val")

    engine = create_engine(url)
    try:
        _assert_check_constraint_accepts_lower_rejects_upper(
            engine, sql_dialect, expr, f"probe_{revision}_{dialect}", _VALID_UUID, _INVALID_UUID,
        )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("dialect", "env_name"),
    [("postgres", "RUNTIME_POSTGRES_URL"), ("mysql", "RUNTIME_MYSQL_URL")],
)
def test_0027_materialization_hash_check_constraint_rejects_uppercase_hex(dialect, env_name):
    """Real MySQL/Postgres proof for 0027's materialization_hash check,
    the sixth CHECK constraint sharing this bug class -- not covered by
    `_uuid_check` since 0027 predates that helper's introduction."""
    url = os.environ.get(env_name)
    if not url:
        pytest.skip(f"{env_name} is not configured")
    pytest.importorskip("psycopg2" if dialect == "postgres" else "pymysql")

    sql_dialect = "postgresql" if dialect == "postgres" else "mysql"
    expr = _materialization_hash_expr(sql_dialect).replace("materialization_hash", "val")

    engine = create_engine(url)
    try:
        _assert_check_constraint_accepts_lower_rejects_upper(
            engine, sql_dialect, expr, f"probe_0027_hash_{dialect}", _VALID_HASH, _INVALID_HASH,
        )
    finally:
        engine.dispose()
