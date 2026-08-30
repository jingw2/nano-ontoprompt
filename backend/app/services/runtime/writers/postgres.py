"""PostgreSQL managed row writer (Task 24).

`PostgresRowWriter.execute` is the first place in this whole Milestone-3
execution path that actually opens a connection to a real database and
writes a row. Everything upstream (Task 21's `freeze_action_target`,
Task 22's `simulate_action`, Task 23's `evaluate_execution_policy`) only
ever proposed, simulated, or routed a plan. This is a genuine security
boundary: every identifier this module ever puts into SQL comes only from
the binding's own fixed `FrozenActionPlan` fields (never a caller-supplied
string), re-validated here independently before any SQL is built; every
business value (primary-key values, write parameters, the expected
pre-write version) is bound as a query parameter, never string-interpolated;
the version-column precondition is always part of the WHERE clause
(optimistic locking); and the database itself must report exactly one
affected row inside the transaction or the whole transaction is rolled
back — a plan that would touch zero or more than one row is never partially
applied.

Credential resolution: no vault integration exists anywhere in this
codebase yet (`ManagedActionBinding.secret_ref`/`FrozenActionPlan` carry only
a reference string — see `action_bindings.py`'s docstring). `credential_ref`
is threaded through a pluggable `credential_resolver` hook so a later task
can drop in real vault resolution without changing this class's shape; the
default resolver is an inert pass-through. Per this task's own interface
contract, the actual database connection for `execute` is always the
`postgres_url` given to the constructor, never a URL derived from resolving
`credential_ref` — a real resolver would be used to fetch short-lived
connection credentials to merge into that connection, not to replace it.
The raw `credential_ref` value is never logged, returned in a `WriteReceipt`,
or embedded in any exception message.
"""
from __future__ import annotations

from typing import Any, Callable

import psycopg2

from app.schemas.runtime import ReasonCode
from .base import FrozenActionPlan, WriteReceipt, WriterError, _hash, _validate_identifier

_DEFAULT_STATEMENT_TIMEOUT_MS = 5_000

CredentialResolver = Callable[[str], None]


def _default_credential_resolver(credential_ref: str) -> None:
    """Inert placeholder: no vault integration exists yet to resolve
    `credential_ref` into anything. A later task can replace this with real
    vault resolution without changing `PostgresRowWriter`'s constructor
    signature or its `execute` contract."""
    return None


def _validate_plan_shape(plan: FrozenActionPlan) -> None:
    """Reject SQL, identifier, connection, target-selector, delete, DDL, and
    multi-target inputs before ever opening a transaction. Everything here
    is decidable purely from `plan`'s own shape — no database round trip is
    ever needed (or performed) to reach one of these verdicts."""
    if plan.dialect != "postgresql":
        raise WriterError(
            ReasonCode.UNSUPPORTED_ACTION.value,
            f"PostgresRowWriter cannot execute a {plan.dialect!r} plan",
        )
    for field, name in (
        ("schema_name", plan.schema_name),
        ("table_name", plan.table_name),
        ("version_column", plan.version_column),
    ):
        _validate_identifier(name, field=field)
    if not plan.primary_key_columns:
        raise WriterError(ReasonCode.UNSUPPORTED_ACTION.value, "primary_key_columns must not be empty")
    for column in plan.primary_key_columns:
        _validate_identifier(column, field="primary_key_columns")
    for column in plan.writable_columns:
        _validate_identifier(column, field="writable_columns")
    if set(plan.primary_key_columns) & set(plan.writable_columns):
        raise WriterError(ReasonCode.UNSUPPORTED_ACTION.value, "primary_key_columns and writable_columns must be disjoint")
    if plan.version_column in plan.writable_columns:
        raise WriterError(ReasonCode.UNSUPPORTED_ACTION.value, "version_column must not be a writable column")

    # Target-selector: exactly one value per primary-key column, matching
    # the binding's own primary_key_columns exactly — never a partial, an
    # extra, or a duplicated column, any of which would make the WHERE
    # clause an ambiguous, potentially multi-row selector.
    selector_columns = [column for column, _ in plan.primary_key_tuple]
    if (
        len(selector_columns) != len(plan.primary_key_columns)
        or set(selector_columns) != set(plan.primary_key_columns)
        or len(set(selector_columns)) != len(selector_columns)
    ):
        raise WriterError(
            ReasonCode.UNSUPPORTED_ACTION.value,
            "primary_key_tuple must supply exactly one value per primary_key_columns entry",
        )

    # This writer only ever builds an UPDATE — never a DELETE — so it must
    # always have at least one column to SET, and every one of those columns
    # must be a column the binding actually allowlisted as writable.
    if not plan.parameters:
        raise WriterError(ReasonCode.UNSUPPORTED_ACTION.value, "parameters must name at least one writable column")
    for column in plan.parameters.keys():
        _validate_identifier(column, field="parameters")
    if not set(plan.parameters.keys()) <= set(plan.writable_columns):
        raise WriterError(ReasonCode.UNSUPPORTED_ACTION.value, "parameters may only name the binding's writable_columns")


def _after_image_hash(plan: FrozenActionPlan) -> str:
    """Identity hash of the post-write state this writer intends to produce:
    the same target identity `before_image_hash`/`version_hash` (Task 21)
    already pin, plus the exact values this write applies. Mirrors
    `action_bindings._hash`'s canonical-JSON convention."""
    return _hash({
        "managed_action_binding_id": plan.managed_action_binding_id,
        "binding_version": plan.binding_version,
        "primary_key_tuple": [list(pair) for pair in plan.primary_key_tuple],
        "parameters": dict(plan.parameters),
        "version_hash": plan.version_hash,
    })


class PostgresRowWriter:
    """Executes exactly one allowlisted, parameterized single-row UPDATE
    against a real PostgreSQL database, with optimistic locking and an
    exact-row-count requirement enforced inside one transaction."""

    def __init__(
        self,
        postgres_url: str,
        *,
        credential_resolver: CredentialResolver | None = None,
        statement_timeout_ms: int = _DEFAULT_STATEMENT_TIMEOUT_MS,
    ):
        self._postgres_url = postgres_url
        self._credential_resolver = credential_resolver or _default_credential_resolver
        self._statement_timeout_ms = int(statement_timeout_ms)

    def execute(self, plan: FrozenActionPlan, *, credential_ref: str) -> WriteReceipt:
        _validate_plan_shape(plan)

        try:
            self._credential_resolver(credential_ref)
        except Exception:  # never let a resolver failure surface credential_ref, even via __cause__
            raise WriterError(ReasonCode.PRECONDITION_CONFLICT.value, "credential resolution failed") from None

        set_clause = ", ".join(f'"{column}" = %({column})s' for column in plan.parameters.keys())
        where_clause = " AND ".join(f'"{column}" = %(pk__{column})s' for column, _ in plan.primary_key_tuple)
        sql = (
            f'UPDATE "{plan.schema_name}"."{plan.table_name}" '
            f"SET {set_clause} "
            f'WHERE {where_clause} AND "{plan.version_column}" = %(expected_version)s'
        )
        params: dict[str, Any] = dict(plan.parameters)
        for column, value in plan.primary_key_tuple:
            params[f"pk__{column}"] = value
        params["expected_version"] = plan.expected_version

        connection = psycopg2.connect(self._postgres_url)
        try:
            connection.autocommit = False
            with connection.cursor() as cursor:
                # Developer-controlled only (never plan/caller input), so
                # direct interpolation of this validated integer is safe.
                cursor.execute(f"SET LOCAL statement_timeout = {self._statement_timeout_ms}")
                cursor.execute(sql, params)
                affected_rows = cursor.rowcount
                if affected_rows != 1:
                    connection.rollback()
                    raise WriterError(
                        ReasonCode.ROW_COUNT_MISMATCH.value,
                        f"expected exactly 1 affected row, got {affected_rows}",
                    )
                connection.commit()
        except psycopg2.Error as exc:
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
        )


__all__ = ["PostgresRowWriter"]
