"""Governed runtime execution, reconciliation, and durable approval (Task 26).

Three append-mostly tables back `app.services.runtime.execution`, the one
place in this codebase that ever calls a `ManagedRowWriter.execute()`
(Task 24/25) for real:

- `RuntimeExecution` is the execution fence: at most one row per `plan_id`
  (a `UNIQUE` constraint), inserted in `PENDING` status *before* a writer is
  ever called, then updated exactly once to a terminal `status`
  (`SUCCEEDED`/`FAILED`/`UNKNOWN`) once the outcome is known. A concurrent
  second `execute_plan` call for the same plan hits the `UNIQUE` constraint
  and is treated as "already executing/executed," never as a fresh attempt.
- `RuntimeReconciliationCase` records an `UNKNOWN` outcome (an ambiguous
  timeout/connection failure where whether the write committed cannot be
  determined) for human review. This is a distinct table from the pre-existing
  P5C `agent_reconciliation_cases` (a different domain — Agent tool-call
  executions, not `RuntimePlan`s); see `app.services.runtime.reconciliation`'s
  module docstring for the split rationale.
- `RuntimeExecutionApproval` durably persists what Task 23's
  `approve_exact_plan` (`app.services.actions.approval`) already verifies
  in memory but never writes to disk. `approve_exact_plan` itself is
  unchanged — `app.services.runtime.execution.record_plan_approval` calls it
  and persists the resulting `ApprovalReceipt`'s fields here, so
  `execute_plan` has a real, durable fact to check for a `HUMAN_APPROVED`
  plan before ever calling a writer, instead of trusting an unpersisted,
  point-in-time verdict it cannot re-observe.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Unlike sibling ORM models (e.g. `RuntimePlan`/`ManagedActionBinding`), none
# of these three tables declare a PostgreSQL-only `id ~ '<uuid-regex>'` CHECK
# constraint here. `RuntimeExecution.plan_id`'s `UniqueConstraint` is
# load-bearing — it IS the execution fence a concurrent second
# `execute_plan` call must hit. The unit-test SQLite harness
# (`tests/conftest.py:_create_sqlite_compatible_tables`) compiles each
# table's real DDL first and falls back to a columns-only table (no CHECK,
# no UNIQUE, no FK at all) the moment any single constraint fails to execute
# on SQLite — and `~` is not valid SQLite syntax. Keeping a `~` CHECK here
# would silently drop the fence's own `UniqueConstraint` under the unit-test
# harness. UUID format is still enforced correctly, per-dialect, by the
# accompanying migration (`0034_runtime_execution.py`'s `_uuid_check`).


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class RuntimeExecution(Base):
    """One governed execution attempt for exactly one `RuntimePlan`."""

    __tablename__ = "runtime_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')",
            name="ck_runtime_executions_status",
        ),
        CheckConstraint(
            "execution_class IN ('AUTOMATIC', 'HUMAN_APPROVED')",
            name="ck_runtime_executions_execution_class",
        ),
        CheckConstraint("dialect IN ('postgresql', 'mysql')", name="ck_runtime_executions_dialect"),
        # The execution fence: at most one execution row per plan, ever.
        UniqueConstraint("plan_id", name="uq_runtime_executions_plan_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runtime_plans.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_class: Mapped[str] = mapped_column(String(20), nullable=False)
    dialect: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    # A `WriteReceipt`'s own fields on success, or a small redacted-safe
    # failure descriptor (e.g. {"reason_code": "ROW_COUNT_MISMATCH"}) on a
    # definite failure. Never a secret or credential value either way.
    writer_receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    audit_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    # No DB-level FK to `runtime_reconciliation_cases` here on purpose: that
    # table itself carries a real FK back to this one (`execution_id`), and a
    # mutual FK pair between two tables created in the same migration is
    # unnecessary complexity for a pointer that is only ever set once, after
    # the case row already exists.
    reconciliation_case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class RuntimeReconciliationCase(Base):
    """One human-review case for an `UNKNOWN` execution outcome.

    Distinct from the pre-existing P5C `agent_reconciliation_cases`
    (`app.services.runtime.reconciliation`'s original tool-call-execution
    domain) — this table's subject is a `RuntimeExecution`/`RuntimePlan`
    pair, not an Agent turn/tool-call execution.
    """

    __tablename__ = "runtime_reconciliation_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'resolved_succeeded', 'resolved_failed')",
            name="ck_runtime_reconciliation_cases_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runtime_executions.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runtime_plans.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    unknown_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    observed_effect: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    next_action: Mapped[str] = mapped_column(String(100), nullable=False, default="human_review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class RuntimeExecutionApproval(Base):
    """Durable persistence of one `ApprovalReceipt` (Task 23's
    `approve_exact_plan`, `app.services.actions.approval`) — see
    `app.services.runtime.execution.record_plan_approval`. `approve_exact_plan`
    itself remains a stateless, re-verified-every-time function (unchanged);
    this table is what lets `execute_plan` find a durable, previously-recorded
    approval for a `HUMAN_APPROVED` plan instead of trusting an ephemeral
    verdict it has no way to re-observe.
    """

    __tablename__ = "runtime_execution_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runtime_plans.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approver_agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    approver_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class GovernedTurnPlan(Base):
    """One governed-action proposal created FROM AN AGENT TURN'S OWN
    persisted evidence (Task 3, business-journey acceptance), not from an
    `ActionPlanRequest`/investigation like `RuntimePlan`.

    This is a deliberately separate table from both `RuntimePlan` (which
    requires a materialized `SemanticSnapshot` + a catalog `Action` row —
    neither of which a plain conversational Agent turn produces) and
    `AgentApproval` (which is wired into the SAME turn's OWN dispatch/resume
    state machine — reusing it for three independent UI-initiated proposals
    against three different targets would incorrectly resume/requeue that
    turn three times). A `GovernedTurnPlan` never touches `agent_turns.status`
    or `runtime_plans` at all; it only READS `agent_tool_executions`/
    `agent_messages` as evidence and stands on its own for the
    approve/reject/expire decision.

    `idempotency_key` is globally unique, so a caller replaying the same key
    (whether or not the rest of the payload matches) is a real constraint
    violation, not a value `create_governed_plan_from_turn` could silently
    interpret as "replay the existing plan" — the browser is required to
    mint a fresh key per branch, and reusing one is always rejected.
    """

    __tablename__ = "governed_turn_plans"
    __table_args__ = (
        CheckConstraint("branch IN ('approved', 'rejected', 'expired')", name="ck_governed_turn_plans_branch"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_governed_turn_plans_status"),
        UniqueConstraint("idempotency_key", name="uq_governed_turn_plans_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    tool_execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_tool_executions.id", ondelete="RESTRICT"), nullable=False,
    )
    branch: Mapped[str] = mapped_column(String(20), nullable=False)
    target_fixture_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    target_before_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_after_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    audit_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_by_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "GovernedTurnPlan",
    "RuntimeExecution",
    "RuntimeReconciliationCase",
    "RuntimeExecutionApproval",
]
