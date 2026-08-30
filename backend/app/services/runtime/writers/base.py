"""Managed row writer contracts (Task 24).

This is the first module in the whole Milestone-3 execution path that
describes what it means to actually open a connection to a real database
and write one row. Every task before this one (`app.services.runtime.
action_bindings`'s `freeze_action_target` — Task 21, `app.services.runtime.
sandbox`'s `simulate_action` — Task 22, `app.services.runtime.risk`'s
`evaluate_execution_policy` — Task 23) only ever proposed, simulated, or
routed a plan; none of them ever opened a connector. A `ManagedRowWriter`
is the governed boundary where that finally happens, so it is held to a
stricter discipline than anything upstream: it trusts nothing about *how*
its input was produced, and re-validates every identifier and target shape
for itself before ever building SQL — the same defense-in-depth discipline
`action_bindings._validate_identifier` already applies one layer up (see
that module's docstring), repeated here one layer closer to the actual
database call.

`FrozenActionPlan` is this module's input type — everything a writer needs
to build exactly one safe, parameterized UPDATE without looking anything
else up. It is not constructed here: composing it from a real `RuntimePlan`
plus its resolved `ManagedActionBinding` is a later task's job (this task's
own tests construct it directly). Its fields are `FrozenTarget`'s own
fields (`app.services.runtime.action_bindings`, Task 21) plus the binding
identity/shape a writer cannot do without and the plan's `idempotency_key`.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class WriterError(Exception):
    """Structured denial for a managed row write. `reason_code` is always
    one of `app.schemas.runtime.ReasonCode`'s values — stable and safe to
    surface to a caller — mirroring `BindingError` (Task 21) and
    `SandboxError` (Task 22). Never carries a credential or secret value in
    its message."""

    def __init__(self, reason_code: str, message: str | None = None):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


def _validate_identifier(name: object, *, field: str) -> None:
    """Same identifier allowlist `action_bindings._validate_identifier`
    already enforces at publish time (Task 21) — re-checked here,
    independently, at the writer boundary, because this module never trusts
    that an identifier arriving on a `FrozenActionPlan` is safe merely
    because some earlier stage already validated it."""
    if not isinstance(name, str) or not _IDENTIFIER_PATTERN.match(name):
        raise WriterError("UNSUPPORTED_ACTION", f"invalid identifier for {field}: {name!r}")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def validate_plan_shape(plan: "FrozenActionPlan", *, expected_dialect: str) -> None:
    """Reject SQL, identifier, connection, target-selector, delete, DDL, and
    multi-target inputs before ever opening a transaction. Everything here is
    decidable purely from `plan`'s own shape — no database round trip is ever
    needed (or performed) to reach one of these verdicts. Dialect-independent:
    every `ManagedRowWriter` implementation (Task 24's `PostgresRowWriter`,
    Task 25's `MySQLRowWriter`) calls this same function with its own
    `expected_dialect`, so this structural validation logic lives in exactly
    one place rather than as separate copies that could silently drift apart."""
    if plan.dialect != expected_dialect:
        raise WriterError(
            "UNSUPPORTED_ACTION",
            f"a {expected_dialect!r} writer cannot execute a {plan.dialect!r} plan",
        )
    for field, name in (
        ("schema_name", plan.schema_name),
        ("table_name", plan.table_name),
        ("version_column", plan.version_column),
    ):
        _validate_identifier(name, field=field)
    if not plan.primary_key_columns:
        raise WriterError("UNSUPPORTED_ACTION", "primary_key_columns must not be empty")
    for column in plan.primary_key_columns:
        _validate_identifier(column, field="primary_key_columns")
    for column in plan.writable_columns:
        _validate_identifier(column, field="writable_columns")
    if set(plan.primary_key_columns) & set(plan.writable_columns):
        raise WriterError("UNSUPPORTED_ACTION", "primary_key_columns and writable_columns must be disjoint")
    if plan.version_column in plan.writable_columns:
        raise WriterError("UNSUPPORTED_ACTION", "version_column must not be a writable column")

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
            "UNSUPPORTED_ACTION",
            "primary_key_tuple must supply exactly one value per primary_key_columns entry",
        )

    # This writer only ever builds an UPDATE — never a DELETE — so it must
    # always have at least one column to SET, and every one of those columns
    # must be a column the binding actually allowlisted as writable.
    if not plan.parameters:
        raise WriterError("UNSUPPORTED_ACTION", "parameters must name at least one writable column")
    for column in plan.parameters.keys():
        _validate_identifier(column, field="parameters")
    if not set(plan.parameters.keys()) <= set(plan.writable_columns):
        raise WriterError("UNSUPPORTED_ACTION", "parameters may only name the binding's writable_columns")


@dataclass(frozen=True)
class FrozenActionPlan:
    """Everything a `ManagedRowWriter` needs to build one safe, parameterized
    single-row UPDATE. Every identifier field here names a real schema/table/
    column on a governed `ManagedActionBinding` (Task 21) — a writer must
    still re-validate each one itself (see `_validate_identifier` above)
    rather than trust that upstream already did.

    `primary_key_tuple` and `parameters` mirror `FrozenTarget`
    (`action_bindings.py`) exactly: an ordered, server-resolved primary-key
    identity and the binding's own typed, allowlisted writable values.
    `expected_version` is the optimistic-locking precondition value a writer
    must include in its WHERE clause alongside the primary key — the value
    `version_column` was known to hold when this plan was frozen; a live row
    whose `version_column` no longer matches it is, by definition, a target
    that drifted since this plan was frozen, and must never be written to.
    """

    managed_action_binding_id: str
    binding_version: int
    connection_target_identity: str
    dialect: str
    schema_name: str
    table_name: str
    primary_key_columns: tuple[str, ...]
    writable_columns: tuple[str, ...]
    version_column: str
    primary_key_tuple: tuple[tuple[str, str], ...]
    parameters: Mapping[str, Any]
    expected_version: Any
    before_image_hash: str
    version_hash: str
    idempotency_key: str


@dataclass(frozen=True)
class WriteReceipt:
    """Proof of exactly one governed row write. Never carries `credential_ref`
    or any secret value — a writer resolves a credential only to open its
    own connection, and that value is never serialized onto anything this
    module returns, logs, or raises."""

    dialect: str
    primary_key_tuple: tuple[tuple[str, str], ...]
    affected_rows: int
    before_image_hash: str
    after_image_hash: str
    version_hash: str
    idempotency_key: str
    status: str
    correlation_id: str


@runtime_checkable
class ManagedRowWriter(Protocol):
    """One governed row write per call. Implementations must: name only the
    binding's own identifiers (never a caller-supplied one), bind every
    value as a query parameter, enforce the version-column precondition,
    require exactly one affected row, and roll back otherwise."""

    def execute(self, plan: FrozenActionPlan, *, credential_ref: str) -> WriteReceipt: ...


__all__ = [
    "WriterError",
    "FrozenActionPlan",
    "WriteReceipt",
    "ManagedRowWriter",
    "validate_plan_shape",
]
