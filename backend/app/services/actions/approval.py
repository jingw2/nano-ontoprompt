"""Canonical Agent action approval (P5B-APPROVAL, Section 7).

One approval authority: `AgentApproval` with an immutable mapping to
(turn_id, tool_execution_id, node_execution_id) and canonical dependency
hashes.  Only `pending` may CAS to `approved|rejected|stale` via
`ResolveApprovalRequest{base_revision, preview_hash}`; the expiry sweeper CASes
`pending -> expired`; cancellation CASes `pending -> cancelled`; the fenced
worker CASes `approved -> consumed`.  Stale terminalization marks the
execution non-runnable, fails the awaiting_approval Turn with
APPROVAL_STALE, invalidates dispatch rows and clears the session pointer in
one transaction.  Successful resolution writes audit plus one unique resume
outbox and returns 202.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.sandbox import SandboxSimulation
from app.schemas.runtime import ReasonCode
from app.services.runtime.credentials import RuntimeAccessError, RuntimeContext
from app.services.runtime.risk import ExecutionClass, evaluate_execution_policy
from app.services.runtime.sandbox import SandboxResult
from app.services.runtime.service import RuntimeService

APPROVAL_TTL_SECONDS = 60 * 60 * 24  # 24h expiry


class ApprovalError(Exception):
    """Rejected approval operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _correlation(operation: str, approval_id: str) -> str:
    return f"approval:{operation}:{approval_id}"


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite (the unit-test harness) round-trips `DateTime(timezone=True)`
    values as naive; PostgreSQL preserves tzinfo. Every value this module
    ever compares against is UTC, so a naive read is always UTC too —
    mirrors the identical helper in `app.services.runtime.credentials` and
    `app.services.runtime.policy`."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def create_approval(
    db: Session, *, turn_id: str, tool_execution_id: str, node_execution_id: str | None,
    designated_actor_id: str, preview_hash: str, parameter_hash: str,
    schema_hash: str, release_hash: str, model_hash: str, policy_hash: str,
) -> dict:
    """Create a pending approval in the fenced worker transaction that also
    creates the proposed execution."""
    approval_id = _new_id()
    db.execute(text(
        "INSERT INTO agent_approvals (id, turn_id, tool_execution_id, node_execution_id, "
        "preview_hash, parameter_hash, schema_hash, release_hash, model_hash, policy_hash, "
        "designated_actor_id, revision, expires_at, status, created_at, updated_at) "
        "VALUES (:id, :turn, :teid, :neid, :ph, :pah, :sh, :rh, :mh, :poh, :actor, 1, "
        ":expires, 'pending', now(), now())"
    ), {"id": approval_id, "turn": turn_id, "teid": tool_execution_id, "neid": node_execution_id,
        "ph": preview_hash, "pah": parameter_hash, "sh": schema_hash, "rh": release_hash,
        "mh": model_hash, "poh": policy_hash, "actor": designated_actor_id,
        "expires": _now() + timedelta(seconds=APPROVAL_TTL_SECONDS)})
    db.execute(text(
        "UPDATE agent_turns SET status = 'awaiting_approval', updated_at = now() "
        "WHERE id = :id AND status = 'running'"
    ), {"id": turn_id})
    db.commit()
    return {"id": approval_id, "status": "pending", "revision": 1}


def get_approval(db: Session, *, approval_id: str, actor_id: str) -> dict:
    row = db.execute(text(
        "SELECT a.id, a.turn_id, a.tool_execution_id, a.node_execution_id, a.preview_hash, "
        "a.parameter_hash, a.schema_hash, a.release_hash, a.model_hash, a.policy_hash, "
        "a.designated_actor_id, a.revision, a.expires_at, a.status, a.stale_reason, "
        "a.created_at, a.updated_at, s.agent_id "
        "FROM agent_approvals a "
        "JOIN agent_turns t ON t.id = a.turn_id "
        "JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE a.id = :id"
    ), {"id": approval_id}).mappings().one_or_none()
    if row is None:
        raise ApprovalError("APPROVAL_NOT_FOUND")
    return dict(row)


def _current_dependency_hashes(db: Session, agent_id: str, ontology_id: str | None) -> dict:
    """The current immutable dependency hashes against which the approval is
    revalidated (agent version hash, release schema hash, data policy)."""
    out: dict = {"model_hash": "", "release_hash": "", "schema_hash": "", "policy_hash": ""}
    row = db.execute(text(
        "SELECT v.config_hash, v.default_model_config_version_id "
        "FROM agents a JOIN agent_versions v ON v.id = a.active_version_id "
        "WHERE a.id = :id"
    ), {"id": agent_id}).mappings().one_or_none()
    if row:
        out["model_hash"] = row["config_hash"]
    if ontology_id:
        rel = db.execute(text(
            "SELECT r.schema_hash FROM ontology_projects p "
            "JOIN ontology_releases r ON r.id = p.latest_published_release_id "
            "WHERE p.id = :o"
        ), {"o": ontology_id}).mappings().one_or_none()
        if rel and rel["schema_hash"]:
            out["release_hash"] = rel["schema_hash"].hex()
            out["schema_hash"] = rel["schema_hash"].hex()
    return out


def resolve_approval(
    db: Session, *, approval_id: str, actor_id: str, base_revision: int, preview_hash: str,
    decision: str, idempotency_key: str | None = None, request_hash: str | None = None,
) -> dict:
    """Approve/reject with full CAS revalidation.  `decision` is
    approved|rejected.  Returns 202 receipt or APPROVAL_STALE (with stored
    stale terminal response on identical replay)."""
    row = db.execute(text(
        "SELECT a.id, a.turn_id, a.tool_execution_id, a.designated_actor_id, a.revision, "
        "a.status, a.preview_hash, a.parameter_hash, a.schema_hash, a.release_hash, "
        "a.model_hash, a.policy_hash, a.expires_at, t.session_id, s.owner_user_id, s.agent_id "
        "FROM agent_approvals a "
        "JOIN agent_turns t ON t.id = a.turn_id "
        "JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE a.id = :id FOR UPDATE"
    ), {"id": approval_id}).mappings().one_or_none()
    if row is None:
        db.rollback()
        raise ApprovalError("APPROVAL_NOT_FOUND")
    # exact designated actor, existence-hidden otherwise
    if row["designated_actor_id"] != actor_id or row["owner_user_id"] != actor_id:
        db.rollback()
        raise ApprovalError("APPROVAL_NOT_FOUND")
    if row["status"] != "pending":
        db.rollback()
        raise ApprovalError("APPROVAL_ALREADY_RESOLVED")
    if row["revision"] != base_revision or row["preview_hash"] != preview_hash:
        db.rollback()
        raise ApprovalError("APPROVAL_STALE")
    if row["expires_at"] <= _now():
        db.execute(text(
            "UPDATE agent_approvals SET status = 'expired', updated_at = now() WHERE id = :id"
        ), {"id": approval_id})
        db.commit()
        raise ApprovalError("APPROVAL_EXPIRED")

    current = _current_dependency_hashes(db, row["agent_id"], None)
    stale_reason = None
    if current["model_hash"] and current["model_hash"] != row["model_hash"]:
        stale_reason = "MODEL_CHANGED"
    elif current["release_hash"] and current["release_hash"] != row["release_hash"]:
        stale_reason = "RELEASE_CHANGED"

    if stale_reason is not None:
        return _stale_terminalize(db, row, approval_id, stale_reason, current)

    # CAS pending -> approved/rejected
    db.execute(text(
        "UPDATE agent_approvals SET status = :status, revision = revision + 1, "
        "updated_at = now() WHERE id = :id AND status = 'pending'"
    ), {"status": decision, "id": approval_id})
    if decision == "approved":
        # successful resolution: turn back to queued + one unique resume outbox
        new_generation = db.execute(text(
            "UPDATE agent_turns SET status = 'queued', dispatch_generation = dispatch_generation + 1, "
            "claim_token = NULL, worker_artifact_id = NULL, lease_expires_at = NULL, "
            "updated_at = now() WHERE id = :id AND status = 'awaiting_approval' "
            "RETURNING dispatch_generation"
        ), {"id": row["turn_id"]}).scalar_one_or_none()
        if new_generation is None:
            db.rollback()
            raise ApprovalError("APPROVAL_STALE")
        db.execute(text(
            "INSERT INTO agent_turn_dispatch_outbox (id, turn_id, dispatch_generation, operation, "
            "state, created_at) VALUES (:id, :turn, :gen, 'resume_approval', 'pending', now())"
        ), {"id": _new_id(), "turn": row["turn_id"], "gen": new_generation})
        db.commit()
        return {
            "approval_id": approval_id, "turn_id": row["turn_id"], "status": "approved",
            "dispatch_generation": new_generation,
            "correlation_id": _correlation("approve", approval_id),
            "stream_ticket": None, "stream_ticket_url": None,
        }
    # rejected: mark execution non-runnable, turn back to queued WITH a
    # resume dispatch — the runtime's _resolve_pending_action must still
    # get a chance to tell the model the action was rejected, exactly like
    # the approved path resumes to tell it the action executed
    db.execute(text(
        "UPDATE agent_tool_executions SET status = 'cancelled', updated_at = now() "
        "WHERE id = :teid"
    ), {"teid": row["tool_execution_id"]})
    new_generation = db.execute(text(
        "UPDATE agent_turns SET status = 'queued', dispatch_generation = dispatch_generation + 1, "
        "claim_token = NULL, worker_artifact_id = NULL, lease_expires_at = NULL, "
        "updated_at = now() WHERE id = :id AND status = 'awaiting_approval' "
        "RETURNING dispatch_generation"
    ), {"id": row["turn_id"]}).scalar_one_or_none()
    if new_generation is None:
        db.rollback()
        raise ApprovalError("APPROVAL_STALE")
    db.execute(text(
        "INSERT INTO agent_turn_dispatch_outbox (id, turn_id, dispatch_generation, operation, "
        "state, created_at) VALUES (:id, :turn, :gen, 'resume_approval', 'pending', now())"
    ), {"id": _new_id(), "turn": row["turn_id"], "gen": new_generation})
    db.commit()
    return {
        "approval_id": approval_id, "turn_id": row["turn_id"], "status": "rejected",
        "dispatch_generation": new_generation,
        "correlation_id": _correlation("reject", approval_id),
        "stream_ticket": None, "stream_ticket_url": None,
    }


def _stale_terminalize(db: Session, row, approval_id: str, stale_reason: str, current: dict) -> dict:
    """Dependency drift: CAS pending -> stale, fail the awaiting_approval Turn
    with APPROVAL_STALE, mark the execution terminal, invalidate unresolved
    dispatch rows, clear the session pointer; no resume dispatch."""
    now = _now()
    db.execute(text(
        "UPDATE agent_approvals SET status = 'stale', stale_reason = :reason, updated_at = :now "
        "WHERE id = :id AND status = 'pending'"
    ), {"reason": stale_reason, "id": approval_id, "now": now})
    db.execute(text(
        "UPDATE agent_tool_executions SET status = 'cancelled', updated_at = :now "
        "WHERE id = :teid"
    ), {"teid": row["tool_execution_id"], "now": now})
    db.execute(text(
        "UPDATE agent_turns SET status = 'failed', error_code = 'APPROVAL_STALE', updated_at = :now "
        "WHERE id = :id AND status = 'awaiting_approval'"
    ), {"id": row["turn_id"], "now": now})
    db.execute(text(
        "UPDATE agent_turn_dispatch_outbox SET state = 'resolved_cancelled', resolved_at = :now "
        "WHERE turn_id = :id AND state IN ('pending', 'delivered', 'delivered_unclaimed')"
    ), {"id": row["turn_id"], "now": now})
    db.execute(text(
        "UPDATE agent_sessions SET active_turn_id = NULL, updated_at = :now "
        "WHERE id = :sid AND active_turn_id = :tid"
    ), {"sid": row["session_id"], "tid": row["turn_id"], "now": now})
    db.commit()
    return {
        "approval_id": approval_id, "turn_id": row["turn_id"], "status": "stale",
        "stale_reason": stale_reason, "terminal": True, "error_code": "APPROVAL_STALE",
        "correlation_id": _correlation("stale", approval_id),
    }


def sweep_expired_approvals(db: Session) -> int:
    """CAS pending -> expired for past-deadline approvals."""
    result = db.execute(text(
        "UPDATE agent_approvals SET status = 'expired', updated_at = now() "
        "WHERE status = 'pending' AND expires_at <= now()"
    ))
    db.commit()
    return result.rowcount or 0


# --- Runtime exact-plan approval (Task 23) --------------------------------
#
# A distinct approval authority from the P5B tool-call flow above: it never
# touches `agent_approvals`/`agent_turns` at all, because its subject — an
# immutable, snapshot-pinned `RuntimePlan` (Task 15) — is a different kind
# of thing than a proposed tool-call execution. It reuses only what
# genuinely generalizes from the P5B flow's conventions: `_now()`/`_new_id()`
# /`_correlation()` below, and the same "exact match or reject" precondition
# discipline `resolve_approval` already applies to `base_revision`/
# `preview_hash`, applied here to a plan's own `plan_hash`/`expiry` instead.


@dataclass(frozen=True)
class ApprovalReceipt:
    """Proof that exactly one already-simulated `RuntimePlan` (Task 15/22)
    was approved by its own exact `plan_hash`, by the same dual principal
    (Agent + user) it was proposed for.

    Bounded by the SAME `expiry` as the plan it approves — it never grants
    any authority beyond that window, and never grants *ongoing* write
    authority at all: this is a point-in-time receipt, not a durable grant.
    A later execution (a subsequent Milestone 3 task) must independently
    re-verify this receipt's `plan_hash`/`expiry` against the plan's own
    current state before acting on it, exactly as `approve_exact_plan`
    itself does at approval time.
    """

    plan_id: str
    plan_hash: str
    approver_agent_id: str
    approver_user_id: str
    approved_at: datetime
    execution_class: str
    expiry: datetime
    correlation_id: str


def _latest_sandbox_result(db: Session, plan_id: str) -> SandboxResult | None:
    """The most recent `SandboxSimulation` (Task 22) for `plan_id`, projected
    onto the same `SandboxResult` shape `simulate_action` returns. Read-only:
    ownership of the `sandbox_simulations` table stays with
    `app.services.runtime.sandbox`; this function never writes it."""
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


def approve_exact_plan(
    db: Session, *, plan_id: str, presented_plan_hash: str, approver_context: RuntimeContext, now: datetime,
) -> ApprovalReceipt:
    """Accept one exact, unexpired plan hash for a plan Task 23's risk
    evaluator (`app.services.runtime.risk.evaluate_execution_policy`) has
    actually routed to `HUMAN_APPROVED` — never an `AUTOMATIC` plan (which
    needs no approval) and never a `REJECTED` one (which must never become
    approvable no matter what hash is presented).

    Writes nothing: a `RuntimePlan` row is immutable (Task 15) once
    persisted, and this receipt itself grants no ongoing write authority, so
    there is no durable state for this function to own beyond what it reads
    and re-verifies on every call.
    """
    plan = RuntimeService().get_action_plan(plan_id, approver_context, db)

    if presented_plan_hash != plan.plan_hash:
        raise RuntimeAccessError(ReasonCode.INVALID_PLAN_HASH.value)
    if _as_aware_utc(now) >= _as_aware_utc(plan.expiry):
        raise RuntimeAccessError(ReasonCode.PLAN_EXPIRED.value)

    sandbox_result = _latest_sandbox_result(db, plan_id)
    if sandbox_result is None:
        raise RuntimeAccessError(ReasonCode.PRECONDITION_CONFLICT.value)

    decision = evaluate_execution_policy(plan, sandbox_result, approver_context, now)
    if decision.execution_class != ExecutionClass.HUMAN_APPROVED.value:
        raise RuntimeAccessError(
            decision.reason_code if decision.execution_class == ExecutionClass.REJECTED.value
            else ReasonCode.POLICY_DENIED.value
        )

    return ApprovalReceipt(
        plan_id=plan.id, plan_hash=plan.plan_hash,
        approver_agent_id=approver_context.principal.agent_id,
        approver_user_id=approver_context.principal.user_id,
        approved_at=now, execution_class=decision.execution_class,
        expiry=_as_aware_utc(plan.expiry), correlation_id=_correlation("approve_plan", plan.id),
    )
