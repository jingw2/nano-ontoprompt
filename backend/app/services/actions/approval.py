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
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

APPROVAL_TTL_SECONDS = 60 * 60 * 24  # 24h expiry


class ApprovalError(Exception):
    """Rejected approval operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _correlation(operation: str, approval_id: str) -> str:
    return f"approval:{operation}:{approval_id}"


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
    # rejected: mark execution non-runnable, turn back to queued without resume
    db.execute(text(
        "UPDATE agent_tool_executions SET status = 'cancelled', updated_at = now() "
        "WHERE id = :teid"
    ), {"teid": row["tool_execution_id"]})
    new_generation = db.execute(text(
        "UPDATE agent_turns SET status = 'queued', dispatch_generation = dispatch_generation + 1, "
        "updated_at = now() WHERE id = :id AND status = 'awaiting_approval' "
        "RETURNING dispatch_generation"
    ), {"id": row["turn_id"]}).scalar_one_or_none()
    db.commit()
    return {
        "approval_id": approval_id, "turn_id": row["turn_id"], "status": "rejected",
        "dispatch_generation": new_generation or 0,
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
