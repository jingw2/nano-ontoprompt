"""Governed runtime execution (Task 26): the capstone of Milestone 3's
writable-action path.

`execute_plan` is the ONLY place in this entire codebase that ever calls a
`ManagedRowWriter.execute()` (Task 24/25's `PostgresRowWriter`/
`MySQLRowWriter`) — the one point where a real database connection is
finally opened and a real row is finally written. Everything upstream
(Task 15's `create_action_plan`, Task 21's `freeze_action_target`, Task 22's
`simulate_action`, Task 23's `evaluate_execution_policy`/`approve_exact_plan`)
only ever proposed, froze, simulated, or routed a plan — none of them ever
opened a connector. Because a real, meaningful amount of time can pass
between when a plan/Sandbox/approval was computed and the moment of actual
write, `execute_plan` independently re-verifies every governance boundary
built by every task before it — credential/identity, plan hash, expiry,
Sandbox linkage and precondition hashes, risk routing, binding status/
version/connection-target, snapshot governance/freshness, and any
caller-supplied override — immediately before ever calling a writer, never
trusting that any of those checks still hold just because they held once.

Design decisions this module makes that the brief left open (see
`task-26-report.md` for the full discussion):

1. **HUMAN_APPROVED verification.** Task 23's `approve_exact_plan` is a
   stateless, re-verified-every-time function that writes nothing durable
   (see its own docstring). `execute_plan`'s own signature carries no
   approval-evidence parameter (the brief's interface line is exactly
   `execute_plan(db, *, plan_id, presented_plan_hash, context)`), so it
   cannot accept one either. `record_plan_approval` (below) closes this gap
   additively — it calls `approve_exact_plan` (unchanged) and persists the
   resulting `ApprovalReceipt` into a new `RuntimeExecutionApproval` row
   (`app.models.runtime_execution`); `execute_plan` requires a durable,
   unexpired, exact-plan-hash-matching row here before ever executing a
   `HUMAN_APPROVED` plan.
2. **Execution fence.** `RuntimeExecution.plan_id` carries a `UNIQUE`
   constraint. A `PENDING` row is inserted in its own short transaction
   before any writer is ever constructed; a concurrent second call that
   loses the race on that constraint either returns the now-terminal
   receipt (if the winner already finished) or is denied with
   `PRECONDITION_CONFLICT` (if the winner is still mid-flight) — never a
   fresh execution attempt.
3. **Writer connection resolution.** No per-connection URL/credential
   resolution path exists anywhere in this codebase yet (`ManagedActionBinding
   .secret_ref` is a vault reference only — see `action_bindings.py`'s
   docstring — and no vault integration exists). `_resolve_connection_url`
   reads `RUNTIME_POSTGRES_URL`/`RUNTIME_MYSQL_URL` — the exact same
   environment variables the writers' own integration test suites
   (`tests/runtime/integration/test_{postgres,mysql}_writer_integration.py`)
   already use to reach the real, disposable fixture databases.
4. **Reconciliation.** `UNKNOWN` outcomes open a `RuntimeReconciliationCase`
   (`app.services.runtime.reconciliation`, a distinct authority from the
   pre-existing P5C `agent_reconciliation_cases`). `retry_unknown` always
   returns `False` and no function in this module ever re-invokes a writer
   for a case it describes — a genuinely warranted retry is an entirely new
   governed plan through `RuntimeService.create_action_plan`, never a replay.
5. **Rollback.** `create_rollback_plan` restores `ActionPlan.predicted_diff
   ["before"]` through the normal plan-creation path. When `before` is
   `None` (no retained before-image — true of every plan produced by the
   bound/managed-action-binding path, since Task 21's `freeze_action_target`
   never reads a live row), it fails with `PRECONDITION_CONFLICT` rather
   than fabricating a target.
"""
from __future__ import annotations

import dataclasses
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ontology_release import OntologyRelease
from app.models.runtime_execution import RuntimeExecution, RuntimeExecutionApproval
from app.models.sandbox import SandboxSimulation
from app.schemas.runtime import ReasonCode
from app.services.actions.approval import ApprovalReceipt, approve_exact_plan
from app.services.governance_audit import append_audit
from app.services.idempotency import (
    IdempotencyKeyReusedError,
    canonical_request_hash,
    persist_idempotency,
)
from app.services.runtime.action_bindings import (
    BindingError,
    PlanValidationError,
    resolve_published_binding,
    validate_execution_overrides,
)
from app.services.runtime.credentials import RuntimeAccessError, RuntimeContext
from app.services.runtime.policy import (
    DEFAULT_FRESHNESS_POLICY,
    compute_snapshot_freshness,
    evaluate_access,
    evaluate_snapshot_freshness,
)
from app.services.runtime.reconciliation import ReconciliationCase, create_reconciliation_case
from app.services.runtime.risk import ExecutionClass, evaluate_execution_policy
from app.services.runtime.sandbox import SandboxResult
from app.services.runtime.service import (
    CREATE_PLAN_CAPABILITY,
    ActionPlan,
    ActionPlanRequest,
    RuntimeService,
)
from app.services.runtime.snapshots import SnapshotValidationError, get_snapshot
from app.services.runtime.writers.base import FrozenActionPlan, WriterError
from app.services.runtime.writers.mysql import MySQLRowWriter
from app.services.runtime.writers.postgres import PostgresRowWriter

_RUNTIME_URL_ENV_VAR = {"postgresql": "RUNTIME_POSTGRES_URL", "mysql": "RUNTIME_MYSQL_URL"}


class ExecutionError(Exception):
    """Structured denial raised only for a preflight governance failure —
    always BEFORE any execution fence row is inserted and BEFORE any writer
    is ever constructed. Once the fence exists, `execute_plan` never raises
    again for this call; it returns an `ExecutionReceipt` reflecting
    whatever outcome (`SUCCEEDED`/`FAILED`/`UNKNOWN`) was actually
    determined. `reason_code` mirrors `RuntimeAccessError`/`BindingError`/
    `SandboxError`/`WriterError` — stable, safe to surface to a caller."""

    def __init__(self, reason_code: str, message: str | None = None):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


@dataclass(frozen=True)
class ExecutionReceipt:
    """Immutable read projection of one persisted `RuntimeExecution` row."""

    execution_id: str
    plan_id: str
    plan_hash: str
    status: str  # "SUCCEEDED" | "FAILED" | "UNKNOWN"
    execution_class: str  # "AUTOMATIC" | "HUMAN_APPROVED"
    dialect: str
    writer_receipt: Mapping[str, Any] | None
    audit_id: str | None
    idempotency_key: str
    reconciliation_case_id: str | None


def retry_unknown(receipt: ExecutionReceipt) -> bool:
    """`UNKNOWN` outcomes never structurally support blind replay.

    This function is intentionally incapable of triggering re-execution: it
    always returns `False`, and no other function in this module ever calls
    a writer again for an execution once its outcome is `UNKNOWN` — not even
    if this function's own result is (mis)used to gate a caller's retry
    loop. A human must resolve the reconciliation case
    (`app.services.runtime.reconciliation.resolve_reconciliation_case`) and,
    if a retry is genuinely warranted, submit an entirely new governed plan
    through `RuntimeService.create_action_plan` and execute that new plan —
    never resume this one.
    """
    return False


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite (the unit-test harness) round-trips naive datetimes; every
    value this module compares against is UTC, so a naive read is always
    UTC too. Mirrors the identical helper in `credentials.py`/`policy.py`/
    `risk.py`/`approval.py`."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _latest_sandbox_result(db: Session, plan_id: str) -> SandboxResult | None:
    """The most recent `SandboxSimulation` for `plan_id`. Duplicates
    `app.services.actions.approval._latest_sandbox_result` (a private helper
    in a different module, not exported) rather than importing a private
    name across module boundaries — the same duplication discipline already
    used for `_as_aware_utc` throughout this module family."""
    row = db.execute(
        select(SandboxSimulation)
        .where(SandboxSimulation.action_plan_id == plan_id)
        .order_by(SandboxSimulation.created_at.desc())
    ).scalars().first()
    if row is None:
        return None
    return SandboxResult(
        simulation_id=row.id, action_plan_id=row.action_plan_id, expected_rows=row.expected_rows,
        before_after_diff=dict(row.before_after_diff), impact_summary=dict(row.impact_summary),
        rule_outcome=tuple(row.rule_outcome), policy_result=dict(row.policy_result),
        precondition_hashes=dict(row.precondition_hashes), expires_at=row.expires_at,
    )


def record_plan_approval(
    db: Session, *, plan_id: str, presented_plan_hash: str, approver_context: RuntimeContext,
    now: datetime | None = None,
) -> ApprovalReceipt:
    """Durably persist what `approve_exact_plan` (Task 23) already verifies
    in memory. `approve_exact_plan` itself is unchanged — it remains a
    stateless, re-verified-every-time function callable on its own — this
    wrapper is the only place that writes a `RuntimeExecutionApproval` row,
    giving `execute_plan` a durable fact to check later for a
    `HUMAN_APPROVED` plan (see this module's own docstring, decision 1)."""
    now = now or datetime.now(timezone.utc)
    receipt = approve_exact_plan(
        db, plan_id=plan_id, presented_plan_hash=presented_plan_hash,
        approver_context=approver_context, now=now,
    )
    row = RuntimeExecutionApproval(
        id=str(uuid.uuid4()), plan_id=receipt.plan_id, plan_hash=receipt.plan_hash,
        approver_agent_id=receipt.approver_agent_id, approver_user_id=receipt.approver_user_id,
        approved_at=receipt.approved_at, expiry=receipt.expiry, correlation_id=receipt.correlation_id,
    )
    db.add(row)
    db.commit()
    return receipt


def _find_valid_approval(
    db: Session, *, plan_id: str, plan_hash: str, now: datetime,
) -> RuntimeExecutionApproval | None:
    rows = db.execute(
        select(RuntimeExecutionApproval)
        .where(RuntimeExecutionApproval.plan_id == plan_id, RuntimeExecutionApproval.plan_hash == plan_hash)
        .order_by(RuntimeExecutionApproval.approved_at.desc())
    ).scalars().all()
    for row in rows:
        if _as_aware_utc(now) < _as_aware_utc(row.expiry):
            return row
    return None


def _resolve_connection_url(dialect: str) -> str:
    """No per-connection URL/credential resolution path exists anywhere in
    this codebase (no vault integration; `ManagedActionBinding.secret_ref`
    is a reference only). Mirrors the writers' own integration test suites:
    the actual database URL comes from `RUNTIME_POSTGRES_URL`/
    `RUNTIME_MYSQL_URL`."""
    env_var = _RUNTIME_URL_ENV_VAR.get(dialect)
    if env_var is None:
        raise ExecutionError(ReasonCode.UNSUPPORTED_ACTION.value, f"unsupported dialect: {dialect!r}")
    url = os.environ.get(env_var)
    if not url:
        raise ExecutionError(ReasonCode.UNSUPPORTED_ACTION.value, f"{env_var} is not configured")
    return url


def _writer_for_dialect(dialect: str, url: str):
    if dialect == "postgresql":
        return PostgresRowWriter(url)
    if dialect == "mysql":
        return MySQLRowWriter(url)
    raise ExecutionError(ReasonCode.UNSUPPORTED_ACTION.value, f"unsupported dialect: {dialect!r}")


def _fetch_expected_version(
    dialect: str, url: str, *, schema_name: str, table_name: str, version_column: str,
    primary_key_tuple: tuple[tuple[str, str], ...],
) -> Any:
    """One bounded, read-only lookup of the live target row's
    `version_column` value, immediately before a writer is ever called.

    Nothing upstream of `execute_plan` ever captures a real optimistic-lock
    precondition value: `freeze_action_target` (Task 21) computes only
    identity hashes ("this task builds no connector integration, so there is
    no live external row to fetch a real before-image from yet ... a later
    Milestone 3 task that actually executes a binding is expected to fetch
    and compare a real row against these hashes" — its own docstring), and
    neither writer (Task 24/25) computes `expected_version` itself; both
    only ever consume an already-resolved value on `FrozenActionPlan`. This
    function is that later fetch — a single parameterized SELECT scoped to
    exactly the binding's own allowlisted schema/table/columns, using the
    same identifier-quoting convention as the writer it feeds. Zero or more
    than one matching row is `PRECONDITION_CONFLICT`, discovered here,
    read-only, before any write is attempted.
    """
    where_params = {f"pk__{column}": value for column, value in primary_key_tuple}
    if dialect == "postgresql":
        import psycopg2

        where_clause = " AND ".join(f'"{column}" = %(pk__{column})s' for column, _ in primary_key_tuple)
        sql = f'SELECT "{version_column}" FROM "{schema_name}"."{table_name}" WHERE {where_clause}'
        connection = psycopg2.connect(url)
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, where_params)
                rows = cursor.fetchall()
        finally:
            connection.close()
    elif dialect == "mysql":
        import pymysql

        from app.services.runtime.writers.mysql import _parse_mysql_url

        where_clause = " AND ".join(f"`{column}` = %(pk__{column})s" for column, _ in primary_key_tuple)
        sql = f"SELECT `{version_column}` FROM `{schema_name}`.`{table_name}` WHERE {where_clause}"
        connection = pymysql.connect(**_parse_mysql_url(url), autocommit=True)
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, where_params)
                rows = cursor.fetchall()
        finally:
            connection.close()
    else:
        raise ExecutionError(ReasonCode.UNSUPPORTED_ACTION.value, f"unsupported dialect: {dialect!r}")

    if len(rows) != 1:
        raise ExecutionError(
            ReasonCode.PRECONDITION_CONFLICT.value, f"expected exactly one live target row, found {len(rows)}",
        )
    return rows[0][0]


def _execution_to_receipt(row: RuntimeExecution) -> ExecutionReceipt:
    return ExecutionReceipt(
        execution_id=row.id, plan_id=row.plan_id, plan_hash=row.plan_hash, status=row.status,
        execution_class=row.execution_class, dialect=row.dialect,
        writer_receipt=MappingProxyType(dict(row.writer_receipt)) if row.writer_receipt else None,
        audit_id=row.audit_id, idempotency_key=row.idempotency_key,
        reconciliation_case_id=row.reconciliation_case_id,
    )


def _mark_execution_terminal(
    db: Session, execution: RuntimeExecution, *, status: str,
    writer_receipt: Mapping[str, Any] | None = None, audit_id: str | None = None,
    reconciliation_case_id: str | None = None,
) -> RuntimeExecution:
    execution.status = status
    execution.writer_receipt = dict(writer_receipt) if writer_receipt is not None else None
    execution.audit_id = audit_id
    execution.reconciliation_case_id = reconciliation_case_id
    execution.updated_at = datetime.now(timezone.utc)
    db.add(execution)
    db.commit()
    db.refresh(execution)
    return execution


def execute_plan(
    db: Session, *, plan_id: str, presented_plan_hash: str, context: RuntimeContext,
    selector: Mapping[str, Any] | None = None, parameters: Mapping[str, Any] | None = None,
) -> ExecutionReceipt:
    """Revalidate every governance boundary and, only if every one of them
    still holds, call the shared writer exactly once.

    `selector`/`parameters` exist on this signature purely so a caller's
    override attempt can be REJECTED — never honored. Everything this
    function ever executes was already frozen when the plan was created
    (Task 15/21); nothing supplied here ever replaces it.
    """
    now = datetime.now(timezone.utc)

    # 1. Resolve the plan — existence-hiding and identity-enforcing.
    plan = RuntimeService().get_action_plan(plan_id, context, db)

    # 2. Exact plan hash.
    if presented_plan_hash != plan.plan_hash:
        raise ExecutionError(ReasonCode.INVALID_PLAN_HASH.value, "presented plan hash does not match the plan")

    # 3. Expiry.
    if _as_aware_utc(now) >= _as_aware_utc(plan.expiry):
        raise ExecutionError(ReasonCode.PLAN_EXPIRED.value, "plan has expired")

    # Execution requires a real writer target — checked structurally, ahead
    # of risk routing, since no amount of human approval could ever let an
    # unbound plan reach a writer (there is no `ManagedActionBinding` to
    # build a `FrozenActionPlan` from). This is a foundational precondition
    # beyond the risk evaluator's own "unbound_action" HITL risk factor,
    # which only ever gates unbound *proposals* meant for approval workflows
    # that never intend to execute (see `test_risk_policy.py`'s own unscoped
    # plan fixtures) — never a reason to route toward an approval gate this
    # plan could never actually clear.
    if plan.managed_action_binding_id is None:
        raise ExecutionError(ReasonCode.UNSUPPORTED_ACTION.value, "plan is not bound to a managed action binding")

    # 4. Latest Sandbox result + risk re-evaluation.
    sandbox_result = _latest_sandbox_result(db, plan.id)
    if sandbox_result is None:
        raise ExecutionError(ReasonCode.PRECONDITION_CONFLICT.value, "plan has not been simulated")
    if sandbox_result.expected_rows != 1:
        # A bound plan is, by construction, a single-row write. The risk
        # evaluator only ever turns a non-1 `expected_rows` into a HITL risk
        # factor, never a rejection — this cross-check is execute_plan's own
        # addition, since a bound plan whose Sandbox no longer agrees it is
        # exactly one row is drift this function must not let through.
        raise ExecutionError(
            ReasonCode.PRECONDITION_CONFLICT.value, "bound plan no longer resolves to exactly one row",
        )

    risk_decision = evaluate_execution_policy(plan, sandbox_result, context, now)
    if risk_decision.execution_class == ExecutionClass.REJECTED.value:
        raise ExecutionError(risk_decision.reason_code, "risk re-evaluation rejected this plan")
    execution_class = risk_decision.execution_class

    if execution_class == ExecutionClass.HUMAN_APPROVED.value:
        approval = _find_valid_approval(db, plan_id=plan.id, plan_hash=plan.plan_hash, now=now)
        if approval is None:
            raise ExecutionError(
                ReasonCode.POLICY_DENIED.value,
                "plan requires a recorded human approval before execution — see record_plan_approval",
            )

    # 5. Re-resolve the binding — BINDING_DRIFT on any drift.
    try:
        binding_version = int(plan.binding_version)
    except (TypeError, ValueError) as exc:
        raise ExecutionError(ReasonCode.BINDING_DRIFT.value, "binding_version is not resolvable") from exc
    try:
        binding = resolve_published_binding(db, plan.managed_action_binding_id, binding_version)
    except BindingError as exc:
        raise ExecutionError(exc.reason_code, str(exc)) from exc

    # 6. Snapshot governance/freshness AND a live policy re-check (a caller
    #    whose data grant or Agent/user active status has changed since plan
    #    creation must never slip through on a stale, frozen policy_decision).
    try:
        snapshot = get_snapshot(db, plan.semantic_snapshot_id)
    except SnapshotValidationError as exc:
        raise ExecutionError(ReasonCode.SNAPSHOT_STALE.value, str(exc)) from exc
    release = db.execute(
        select(OntologyRelease).where(OntologyRelease.id == snapshot.ontology_release_id)
    ).scalar_one_or_none()
    if release is None:
        raise ExecutionError(ReasonCode.SNAPSHOT_STALE.value, "release is no longer resolvable")

    access_decision = evaluate_access(
        context, required_capability=CREATE_PLAN_CAPABILITY, snapshot=snapshot,
        ontology_id=release.ontology_id, db=db,
    )
    if not access_decision.allowed:
        raise ExecutionError(ReasonCode.POLICY_DENIED.value, "access policy has drifted since plan creation")

    freshness = compute_snapshot_freshness(snapshot, now=now, policy=DEFAULT_FRESHNESS_POLICY)
    freshness_decision = evaluate_snapshot_freshness(freshness, DEFAULT_FRESHNESS_POLICY)
    if freshness_decision.decision == "DENY":
        raise ExecutionError(ReasonCode.SNAPSHOT_STALE.value, "snapshot freshness has drifted since plan creation")

    # 7. Caller overrides are only ever accepted in order to reject them.
    try:
        validate_execution_overrides(plan, selector, parameters)
    except PlanValidationError as exc:
        raise ExecutionError(exc.reason_code, str(exc)) from exc

    # 8. Idempotency record + execution fence — each its own short
    #    transaction, both strictly before any writer is ever constructed.
    request_hash = canonical_request_hash(
        "EXECUTE", f"runtime:plan:{plan.id}", presented_plan_hash.encode("utf-8"),
    )
    # Actor: the plan's own dual principal, the same identity
    # `evaluate_execution_policy` just re-verified the caller against — so
    # two different Agents delegated for the same user (or vice versa) never
    # collide on one idempotency-key namespace.
    actor_id = f"{plan.agent_id}:{plan.user_id}"
    try:
        persist_idempotency(
            db, key=plan.idempotency_key, actor_id=actor_id, route="runtime:execute_plan", request_hash=request_hash,
        )
    except IdempotencyKeyReusedError as exc:
        raise ExecutionError(ReasonCode.PRECONDITION_CONFLICT.value, "idempotency key reused for a different request") from exc

    execution = RuntimeExecution(
        id=str(uuid.uuid4()), plan_id=plan.id, plan_hash=plan.plan_hash, execution_class=execution_class,
        dialect=binding.dialect, status="PENDING", idempotency_key=plan.idempotency_key,
        correlation_id=context.correlation_id,
    )
    db.add(execution)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(RuntimeExecution).where(RuntimeExecution.plan_id == plan.id)
        ).scalar_one_or_none()
        if existing is not None and existing.status != "PENDING":
            # Another call already drove this plan to a terminal outcome —
            # return that receipt rather than attempting a fresh write.
            return _execution_to_receipt(existing)
        # A genuinely concurrent in-flight attempt (still PENDING). No
        # existing ReasonCode names "already executing"; PRECONDITION_CONFLICT
        # is the closest fit — a precondition ("no execution is already
        # underway for this plan") that no longer holds.
        raise ExecutionError(ReasonCode.PRECONDITION_CONFLICT.value, "plan execution is already in progress")
    db.refresh(execution)

    # 9. Build the FrozenActionPlan — the first and only place in this
    #    codebase that composes one from a real ActionPlan + ManagedActionBinding.
    try:
        connection_url = _resolve_connection_url(binding.dialect)
        expected_version = _fetch_expected_version(
            binding.dialect, connection_url, schema_name=binding.schema_name, table_name=binding.table_name,
            version_column=binding.version_column, primary_key_tuple=tuple(plan.target_key),
        )
    except ExecutionError as exc:
        # A definite, certain outcome discovered post-fence but pre-write
        # (missing configuration, or the live row is not uniquely
        # resolvable) — never ambiguous, never routed to reconciliation.
        _mark_execution_terminal(db, execution, status="FAILED", writer_receipt={"reason_code": exc.reason_code})
        return _execution_to_receipt(execution)

    frozen = FrozenActionPlan(
        managed_action_binding_id=binding.managed_action_binding_id, binding_version=binding.version,
        connection_target_identity=binding.connection_target_identity, dialect=binding.dialect,
        schema_name=binding.schema_name, table_name=binding.table_name,
        primary_key_columns=tuple(binding.primary_key_columns), writable_columns=tuple(binding.writable_columns),
        version_column=binding.version_column, primary_key_tuple=tuple(plan.target_key),
        parameters=dict(plan.parameters), expected_version=expected_version,
        before_image_hash=plan.before_image_hash, version_hash=plan.version_hash,
        idempotency_key=plan.idempotency_key,
    )

    # 10. Call the shared writer — the one and only place this whole module
    #     family ever opens a real database connection to write.
    writer = _writer_for_dialect(binding.dialect, connection_url)
    try:
        write_receipt = writer.execute(frozen, credential_ref=binding.secret_ref)
    except WriterError as exc:
        # 13. Definite failure: the writer itself positively determined the
        #     write did not (and will not) apply — certain, never ambiguous,
        #     never routed to reconciliation.
        _mark_execution_terminal(db, execution, status="FAILED", writer_receipt={"reason_code": exc.reason_code})
        return _execution_to_receipt(execution)
    except Exception as exc:
        # 12. Anything escaping the writer's own documented WriterError/
        #     WriteReceipt contract (a timeout, a dropped connection, ...) is,
        #     by definition, an outcome the writer's own safety net could not
        #     classify — genuinely ambiguous. Never retried automatically.
        case = create_reconciliation_case(
            db, execution_id=execution.id, plan_id=plan.id, unknown_reason=str(exc),
            observed_effect={
                "dialect": binding.dialect, "managed_action_binding_id": binding.managed_action_binding_id,
            },
            next_action="human_review",
        )
        _mark_execution_terminal(db, execution, status="UNKNOWN", reconciliation_case_id=case.id)
        return _execution_to_receipt(execution)

    # 11. Success — record the receipt and append the audit event.
    audit_result = append_audit(
        db.connection(),
        security_domain_id=context.principal.security_domain_id,
        operation="runtime.execute_plan",
        decision=execution_class,
        outcome="SUCCEEDED",
        correlation_id=context.correlation_id,
        actor_user_id=context.principal.user_id,
        lineage={
            "plan_id": plan.id,
            "execution_id": execution.id,
            "managed_action_binding_id": binding.managed_action_binding_id,
            "dialect": binding.dialect,
            "affected_rows": write_receipt.affected_rows,
            "primary_key_tuple": [list(pair) for pair in write_receipt.primary_key_tuple],
        },
    )
    _mark_execution_terminal(
        db, execution, status="SUCCEEDED", writer_receipt=dataclasses.asdict(write_receipt), audit_id=audit_result["id"],
    )
    return _execution_to_receipt(execution)


def get_execution_status(db: Session, *, plan_id: str, context: RuntimeContext) -> ExecutionReceipt | None:
    """Return only authorized status: `get_action_plan` enforces that
    `plan_id` belongs to `context`'s own dual principal (existence-hiding)
    before any `RuntimeExecution` row is ever looked up. `None` means no
    execution has been attempted for this plan yet."""
    plan = RuntimeService().get_action_plan(plan_id, context, db)
    row = db.execute(select(RuntimeExecution).where(RuntimeExecution.plan_id == plan.id)).scalar_one_or_none()
    if row is None:
        return None
    return _execution_to_receipt(row)


def create_rollback_plan(db: Session, *, execution_id: str, context: RuntimeContext) -> ActionPlan:
    """Propose a new governed plan that restores the original plan's
    retained before-image. Never writes directly — it only ever produces a
    new `ActionPlan` proposal through the same `RuntimeService
    .create_action_plan` path any other plan goes through; simulating,
    approving, and executing that new plan are separate, later calls, exactly
    as they are for any freshly created plan.
    """
    execution = db.get(RuntimeExecution, execution_id)
    if execution is None:
        # Existence-hiding, mirroring `RuntimeService.get_action_plan`: a
        # nonexistent execution and one belonging to a different principal
        # (caught by `get_action_plan` below) are indistinguishable.
        raise RuntimeAccessError(ReasonCode.POLICY_DENIED.value)
    original_plan = RuntimeService().get_action_plan(execution.plan_id, context, db)

    before = original_plan.predicted_diff.get("before")
    if before is None:
        # True of every plan produced by the bound/managed-action-binding
        # path today: `freeze_action_target` (Task 21) never reads a live
        # row, so a bound plan's `predicted_diff["before"]` is always `None`.
        # A "compensating descriptor" for this case is out of scope for this
        # task (see task-26-report.md) — this fails with a real, structured,
        # informative error rather than fabricating a rollback target.
        raise ExecutionError(
            ReasonCode.PRECONDITION_CONFLICT.value,
            "no retained before-image for this plan; compensating-descriptor rollback is out of scope",
        )
    restore_parameters = {
        column: before[column] for column in original_plan.parameters.keys() if column in before
    }
    if set(restore_parameters.keys()) != set(original_plan.parameters.keys()):
        raise ExecutionError(
            ReasonCode.PRECONDITION_CONFLICT.value,
            "retained before-image does not cover every column the original plan wrote",
        )

    target_selector = dict(original_plan.target_key)
    binding_version = int(original_plan.binding_version) if original_plan.binding_version is not None else None

    request = ActionPlanRequest(
        semantic_snapshot_id=original_plan.semantic_snapshot_id, action_id=original_plan.action_id,
        parameters=restore_parameters, target_selector=target_selector,
        managed_action_binding_id=original_plan.managed_action_binding_id, binding_version=binding_version,
    )
    return RuntimeService().create_action_plan(request, context, db)


__all__ = [
    "ExecutionError",
    "ExecutionReceipt",
    "ReconciliationCase",
    "retry_unknown",
    "record_plan_approval",
    "execute_plan",
    "get_execution_status",
    "create_rollback_plan",
]
