"""Managed action binding publication and target freezing (Task 21).

A writable `Action` only becomes something safe to eventually execute once
it is bound to a fixed, allowlisted single-target row-update surface — this
module is the one place that: (1) validates and publishes that surface as a
versioned `ManagedActionBinding`, (2) resolves a binding back only if it is
genuinely `published`, non-revoked, and its pinned connection target has not
drifted, (3) turns a caller's target selector and parameters into
server-owned, ordered, typed values plus stable integrity hashes
(`freeze_action_target`), and (4) rejects any execution-time attempt to
supply a different target or parameters than a writable `ActionPlan` already
froze.

This module never opens a connection to postgresql/mysql and never executes
SQL — building and running the actual allowlisted single-target UPDATE is an
explicit non-goal of Task 21, deferred to a later Milestone 3 task. Nothing
here ever accepts or persists a secret value: `secret_ref` is a vault
reference only.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.action import Action
from app.models.managed_action import ManagedActionBinding
from app.models.v2.connection import Connection
from app.schemas.runtime import ReasonCode
from app.schemas.runtime_snapshot import SnapshotView

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_DIALECTS = frozenset({"postgresql", "mysql"})
# `ManagedActionBinding.dialect` uses SQL-dialect vocabulary; `Connection.kind`
# (`app.models.v2.connection.ConnectionKind`) uses connector-kind vocabulary.
# This is the one place that maps between them.
_DIALECT_CONNECTION_KIND = {"postgresql": "postgres", "mysql": "mysql"}
_PARAMETER_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}

_BINDING_NOT_ALLOWLISTED = "BINDING_NOT_ALLOWLISTED"


class BindingError(Exception):
    """Structured denial for a managed action binding operation. `reason_code`
    is stable and safe to surface to a caller — mirrors
    `RuntimeAccessError` (Task 13)."""

    def __init__(self, reason_code: str, message: str | None = None):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


class PlanValidationError(Exception):
    """Raised when an execution caller supplies a target selector or
    parameters that do not exactly reproduce what a writable `ActionPlan`
    already froze. A plan's `plan_hash` certifies exactly one target and
    one set of parameters — nothing supplied at execution time may widen or
    replace either."""

    def __init__(self, reason_code: str, message: str | None = None):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


@dataclass(frozen=True)
class FrozenTarget:
    """The server-owned, snapshot-pinned target and parameters a writable
    `ActionPlan` freezes for one managed action binding."""

    primary_key_tuple: tuple[tuple[str, str], ...]
    parameters: Mapping[str, object]
    before_image_hash: str
    version_hash: str


def _connection_identity(connection: Connection) -> str:
    """A non-secret identity for `connection`'s current target — `kind`,
    `name`, and a fingerprint of `connection.config`'s stored value. The
    fingerprint is a one-way hash of the raw stored config (which may
    itself be an encrypted blob) — this never decrypts or exposes
    `config`'s content, only detects that it changed. This is what
    `connection_target_identity` pins at publish time and what drift is
    detected against at resolve time: a rename OR a config repoint (e.g.
    the same connection silently pointed at a different host/database)
    both change this identity."""
    config_fingerprint = _hash(connection.config or {})
    return f"{connection.kind}://{connection.name}#{config_fingerprint}"


def _validate_identifier(name: object, *, field: str) -> None:
    if not isinstance(name, str) or not _IDENTIFIER_PATTERN.match(name):
        raise BindingError(_BINDING_NOT_ALLOWLISTED, f"invalid identifier for {field}: {name!r}")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _validate_allowlist(
    *, dialect: str, schema_name: str, table_name: str,
    primary_key_columns: list[str], writable_columns: list[str],
    version_column: str, parameter_schema: Mapping[str, object],
) -> None:
    """Phase 3 v1 permits only allowlisted, parameterized, single-target row
    updates — this is the one gate that enforces it at publish time: a safe
    dialect, safe identifiers (defense in depth for the executor a later
    task builds), a primary key disjoint from the writable columns, a
    system-managed version column that is never itself writable, and a
    parameter schema that only ever names writable columns."""
    if dialect not in _ALLOWED_DIALECTS:
        raise BindingError(_BINDING_NOT_ALLOWLISTED, f"unsupported dialect: {dialect!r}")
    if not primary_key_columns or not writable_columns:
        raise BindingError(_BINDING_NOT_ALLOWLISTED, "primary_key_columns and writable_columns are required")
    for field, name in (("schema_name", schema_name), ("table_name", table_name), ("version_column", version_column)):
        _validate_identifier(name, field=field)
    for column in primary_key_columns:
        _validate_identifier(column, field="primary_key_columns")
    for column in writable_columns:
        _validate_identifier(column, field="writable_columns")
    if set(primary_key_columns) & set(writable_columns):
        raise BindingError(_BINDING_NOT_ALLOWLISTED, "primary_key_columns and writable_columns must be disjoint")
    if version_column in writable_columns:
        raise BindingError(_BINDING_NOT_ALLOWLISTED, "version_column must not be a writable column")
    if not set(parameter_schema.keys()) <= set(writable_columns):
        raise BindingError(_BINDING_NOT_ALLOWLISTED, "parameter_schema must only name writable_columns")


def publish_binding(
    db: Session, *, action_id: str, connection_id: str, connection_target_identity: str,
    dialect: str, schema_name: str, table_name: str,
    primary_key_columns: Sequence[str], writable_columns: Sequence[str],
    version_column: str, parameter_schema: Mapping[str, object], secret_ref: str,
) -> ManagedActionBinding:
    """Validate the v1 allowlist and create the next versioned, published
    binding for `action_id`. Never mutates a prior version — each call is a
    new row with `version` one greater than the highest version already
    published for this action."""
    primary_key_columns = list(primary_key_columns)
    writable_columns = list(writable_columns)
    _validate_allowlist(
        dialect=dialect, schema_name=schema_name, table_name=table_name,
        primary_key_columns=primary_key_columns, writable_columns=writable_columns,
        version_column=version_column, parameter_schema=parameter_schema,
    )

    action = db.get(Action, action_id)
    if action is None:
        raise BindingError(ReasonCode.ACTION_NOT_ELIGIBLE.value, f"unknown action: {action_id}")
    connection = db.get(Connection, connection_id)
    if connection is None:
        raise BindingError(_BINDING_NOT_ALLOWLISTED, f"unknown connection: {connection_id}")
    if _DIALECT_CONNECTION_KIND.get(dialect) != connection.kind:
        raise BindingError(_BINDING_NOT_ALLOWLISTED, "dialect does not match the connection's kind")
    if _connection_identity(connection) != connection_target_identity:
        raise BindingError(_BINDING_NOT_ALLOWLISTED, "connection_target_identity does not match the connection")

    next_version = (
        db.execute(
            select(func.max(ManagedActionBinding.version)).where(ManagedActionBinding.action_id == action_id)
        ).scalar() or 0
    ) + 1
    binding = ManagedActionBinding(
        managed_action_binding_id=str(uuid.uuid4()), action_id=action_id, version=next_version,
        status="published", connection_id=connection_id, connection_target_identity=connection_target_identity,
        dialect=dialect, schema_name=schema_name, table_name=table_name,
        primary_key_columns=primary_key_columns, writable_columns=writable_columns,
        version_column=version_column, parameter_schema=dict(parameter_schema), secret_ref=secret_ref,
    )
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return binding


def resolve_published_binding(db: Session, binding_id: str, version: int) -> ManagedActionBinding:
    """Return `binding_id`'s binding only if it is exactly the requested
    `version`, still `published` (never `draft` or `revoked`), and its
    pinned connection target still matches the live `Connection` row it was
    published against. Any other state fails closed with `BINDING_DRIFT` —
    never a partial or best-effort result."""
    binding = db.get(ManagedActionBinding, binding_id)
    if binding is None or binding.version != version or binding.status != "published":
        raise BindingError(ReasonCode.BINDING_DRIFT.value, "binding is not a resolvable published version")
    connection = db.get(Connection, binding.connection_id)
    if connection is None or _connection_identity(connection) != binding.connection_target_identity:
        raise BindingError(ReasonCode.BINDING_DRIFT.value, "connection target has drifted since publication")
    return binding


def _typed_parameters(parameter_schema: Mapping[str, object], parameters: Mapping[str, object]) -> dict:
    """Reject any parameter name the binding did not allowlist and any value
    that does not match its declared type — the only parameters a writable
    plan may ever freeze are ones the binding's publisher explicitly typed."""
    if set(parameters.keys()) != set(parameter_schema.keys()):
        raise BindingError("PARAMETER_SCHEMA_MISMATCH", "parameters must exactly match the binding's parameter_schema")
    typed: dict = {}
    for name, value in parameters.items():
        declared_type = parameter_schema[name]
        python_type = _PARAMETER_TYPES.get(declared_type)
        if python_type is not None and not isinstance(value, python_type):
            raise BindingError(
                "PARAMETER_SCHEMA_MISMATCH", f"parameter {name!r} does not match declared type {declared_type!r}",
            )
        typed[name] = value
    return typed


def freeze_action_target(
    db: Session, *, snapshot: SnapshotView, binding: ManagedActionBinding,
    parameters: Mapping[str, object], selector: Mapping[str, object],
) -> FrozenTarget:
    """Resolve `selector` against `binding`'s own primary-key columns —
    never the caller's key order or any extra key — type-check `parameters`
    against the binding's `parameter_schema`, and hash the resulting target
    identity together with the pinned snapshot and binding version, so any
    later attempt to execute against a drifted snapshot or binding version
    is independently detectable.

    `db` is accepted per this module's interface contract but not queried
    here: this task builds no connector integration, so there is no live
    external row to fetch a real before-image from yet (an explicit
    non-goal — see the module docstring). The before-image/version hashes
    this task computes pin the target's *identity*, not a live external
    value; a later Milestone 3 task that actually executes a binding is
    expected to fetch and compare a real row against these hashes.
    """
    primary_key_columns = list(binding.primary_key_columns)
    if set(selector.keys()) != set(primary_key_columns):
        raise BindingError("TARGET_SELECTOR_MISMATCH", "selector must supply exactly the binding's primary key columns")
    primary_key_tuple = tuple((column, str(selector[column])) for column in primary_key_columns)
    typed_parameters = _typed_parameters(binding.parameter_schema, parameters)

    identity = {
        "managed_action_binding_id": binding.managed_action_binding_id,
        "binding_version": binding.version,
        "semantic_snapshot_id": snapshot.id,
        "primary_key_tuple": [list(pair) for pair in primary_key_tuple],
    }
    before_image_hash = _hash(identity)
    version_hash = _hash({**identity, "materialization_hash": snapshot.materialization_hash})
    return FrozenTarget(
        primary_key_tuple=primary_key_tuple, parameters=typed_parameters,
        before_image_hash=before_image_hash, version_hash=version_hash,
    )


def validate_execution_overrides(
    plan, selector: Mapping[str, object] | None, parameters: Mapping[str, object] | None,
) -> None:
    """An execution caller may never supply a replacement target selector or
    parameters — everything a writable plan will execute was already frozen
    when it was created. Any supplied selector/parameters that do not
    exactly reproduce what `plan` already pins invalidates the plan's own
    `plan_hash` certification and is rejected outright."""
    if selector is not None:
        frozen_target = {key: value for key, value in plan.target_key}
        if dict(selector) != frozen_target:
            raise PlanValidationError(
                ReasonCode.INVALID_PLAN_HASH.value, "target selector does not match the frozen plan target",
            )
    if parameters is not None:
        if dict(parameters) != dict(plan.parameters):
            raise PlanValidationError(
                ReasonCode.INVALID_PLAN_HASH.value, "parameters do not match the frozen plan parameters",
            )


__all__ = [
    "BindingError",
    "PlanValidationError",
    "FrozenTarget",
    "publish_binding",
    "resolve_published_binding",
    "freeze_action_target",
    "validate_execution_overrides",
]
