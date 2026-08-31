"""MySQL managed row writer (Task 25).

The MySQL dialect twin of Task 24's `PostgresRowWriter` — same governed
single-row-UPDATE safety contract (`app.services.runtime.writers.base`'s
`FrozenActionPlan` / `WriteReceipt` / `WriterError` / `ManagedRowWriter`),
against MySQL instead of PostgreSQL. Structural preflight (identifier
validation, disjointness, target-selector shape, parameter allowlisting) is
shared with `PostgresRowWriter` via `base.validate_plan_shape` — this module
only supplies what is genuinely dialect-specific: MySQL identifier quoting
(backticks, and MySQL treats `schema` as a database-name qualifier), the
`pymysql` driver and its `pyformat` (`%(name)s`) paramstyle, and MySQL's own
transaction/lock-timeout mechanism (see `_LOCK_WAIT_TIMEOUT_SECONDS` below).

Credential resolution mirrors `postgres.py` exactly: `credential_ref` is
threaded through a pluggable `credential_resolver` hook (default: inert
pass-through); the actual database connection for `execute` is always
`mysql_url` given to the constructor, never a URL derived from resolving
`credential_ref`. The raw `credential_ref` value is never logged, returned in
a `WriteReceipt`, or embedded in any exception message — a resolver's own
exception is re-raised as `WriterError(..., ...) from None` so its
`__cause__` (which could itself embed `credential_ref`) never leaks.
"""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import unquote, urlsplit

import pymysql

from app.schemas.runtime import ReasonCode
from .base import FrozenActionPlan, WriteReceipt, WriterError, _hash, validate_plan_shape

# MySQL has no direct per-statement equivalent of PostgreSQL's `SET LOCAL
# statement_timeout` that bounds how long an UPDATE may block waiting on a
# row lock (MySQL's `MAX_EXECUTION_TIME` only ever applies to SELECT
# statements, never to DML). The actual failure mode Task 24's own
# timeout-rollback test reproduces — a concurrent holder of the same row's
# lock forcing the write to block — is bounded on MySQL/InnoDB by
# `innodb_lock_wait_timeout`: the number of seconds a transaction will wait
# for a row lock before MySQL raises error 1205 ("Lock wait timeout
# exceeded") and the statement (and, per this writer's own rollback-on-error
# handling, the whole transaction) aborts. This is set per-session, in
# whole seconds (MySQL's own minimum granularity — fractional values are not
# supported).
_DEFAULT_LOCK_WAIT_TIMEOUT_SECONDS = 5

CredentialResolver = Callable[[str], None]


def _default_credential_resolver(credential_ref: str) -> None:
    """Inert placeholder: no vault integration exists yet to resolve
    `credential_ref` into anything. Mirrors `postgres._default_credential_resolver`."""
    return None


def _parse_mysql_url(mysql_url: str) -> dict[str, Any]:
    """`pymysql.connect` takes discrete host/port/user/password/database
    keyword arguments, not a single DSN string (unlike `psycopg2.connect`,
    which accepts a URL directly) — this parses the `mysql+pymysql://...`
    URL this module is always given into those keyword arguments."""
    parsed = urlsplit(mysql_url)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else "",
        "database": parsed.path.lstrip("/"),
    }


def _after_image_hash(plan: FrozenActionPlan) -> str:
    """Identical to `postgres._after_image_hash` — the receipt's identity
    hash convention is dialect-independent."""
    return _hash({
        "managed_action_binding_id": plan.managed_action_binding_id,
        "binding_version": plan.binding_version,
        "primary_key_tuple": [list(pair) for pair in plan.primary_key_tuple],
        "parameters": dict(plan.parameters),
        "version_hash": plan.version_hash,
    })


class MySQLRowWriter:
    """Executes exactly one allowlisted, parameterized single-row UPDATE
    against a real MySQL database, with optimistic locking and an
    exact-row-count requirement enforced inside one transaction."""

    def __init__(
        self,
        mysql_url: str,
        *,
        credential_resolver: CredentialResolver | None = None,
        lock_wait_timeout_seconds: int = _DEFAULT_LOCK_WAIT_TIMEOUT_SECONDS,
    ):
        self._mysql_url = mysql_url
        self._credential_resolver = credential_resolver or _default_credential_resolver
        self._lock_wait_timeout_seconds = int(lock_wait_timeout_seconds)

    def execute(self, plan: FrozenActionPlan, *, credential_ref: str) -> WriteReceipt:
        validate_plan_shape(plan, expected_dialect="mysql")

        try:
            self._credential_resolver(credential_ref)
        except Exception:  # never let a resolver failure surface credential_ref, even via __cause__
            raise WriterError(ReasonCode.PRECONDITION_CONFLICT.value, "credential resolution failed") from None

        set_clause = ", ".join(f"`{column}` = %({column})s" for column in plan.parameters.keys())
        # Every governed write must actually advance the optimistic-lock
        # version, not merely gate on it — otherwise two different plans
        # that both observe the same live `expected_version` can both pass
        # the WHERE-clause precondition and the second write silently
        # clobbers the first with no detected conflict. `plan.version_column`
        # is already validated by `validate_plan_shape` above and is
        # structurally guaranteed disjoint from `plan.parameters`
        # (`writable_columns`), so appending it here can never double-set or
        # collide with a caller-supplied column.
        set_clause += f", `{plan.version_column}` = `{plan.version_column}` + 1"
        where_clause = " AND ".join(f"`{column}` = %(pk__{column})s" for column, _ in plan.primary_key_tuple)
        sql = (
            f"UPDATE `{plan.schema_name}`.`{plan.table_name}` "
            f"SET {set_clause} "
            f"WHERE {where_clause} AND `{plan.version_column}` = %(expected_version)s"
        )
        params: dict[str, Any] = dict(plan.parameters)
        for column, value in plan.primary_key_tuple:
            params[f"pk__{column}"] = value
        params["expected_version"] = plan.expected_version

        connection = pymysql.connect(**_parse_mysql_url(self._mysql_url), autocommit=False)
        try:
            with connection.cursor() as cursor:
                # Developer-controlled only (never plan/caller input), so
                # direct interpolation of this validated integer is safe.
                cursor.execute(f"SET SESSION innodb_lock_wait_timeout = {self._lock_wait_timeout_seconds}")
                cursor.execute(sql, params)
                affected_rows = cursor.rowcount
                if affected_rows != 1:
                    connection.rollback()
                    raise WriterError(
                        ReasonCode.ROW_COUNT_MISMATCH.value,
                        f"expected exactly 1 affected row, got {affected_rows}",
                    )
                connection.commit()
        except pymysql.Error as exc:
            connection.rollback()
            raise WriterError(
                ReasonCode.PRECONDITION_CONFLICT.value, "the database rejected or could not complete the write",
            ) from exc
        finally:
            connection.close()

        return WriteReceipt(
            dialect=plan.dialect,
            primary_key_tuple=plan.primary_key_tuple,
            affected_rows=affected_rows,
            before_image_hash=plan.before_image_hash,
            after_image_hash=_after_image_hash(plan),
            version_hash=plan.version_hash,
            idempotency_key=plan.idempotency_key,
            status="committed",
            correlation_id=f"managed-write:{plan.managed_action_binding_id}:{plan.idempotency_key}",
            expected_version=plan.expected_version,
        )


__all__ = ["MySQLRowWriter"]
