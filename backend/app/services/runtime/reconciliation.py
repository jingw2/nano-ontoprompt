"""Agent reconciliation (P5C-EXECUTE).

Operator resolution of unknown outcomes: `confirm_not_accepted_and_retry`
resumes the Turn with a new dispatch generation; `confirm_succeeded` /
`confirm_failed` terminalize.  Unresolved uncertainty cannot select replay;
a stale base revision conflicts.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


class ReconciliationError(Exception):
    """Rejected reconciliation operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def list_cases(db: Session, *, status: str | None = None, limit: int = 50) -> dict:
    params: dict = {"limit": limit}
    where = ""
    if status:
        where = "WHERE c.state = :status"
        params["status"] = status
    rows = db.execute(text(
        "SELECT c.id, c.turn_id, c.execution_kind, c.execution_id, c.revision, c.state, "
        "c.request_hash, c.created_at, c.updated_at "
        "FROM agent_reconciliation_cases c " + where + " ORDER BY c.created_at LIMIT :limit"
    ), params).mappings().all()
    return {"items": [dict(r) for r in rows], "next_cursor": None, "has_more": False}


def get_case(db: Session, *, case_id: str) -> dict:
    """Section 12 reconciliation detail: the redacted persisted case row."""
    row = db.execute(text(
        "SELECT id, turn_id, execution_kind, execution_id, revision, state, "
        "request_hash, created_at, updated_at "
        "FROM agent_reconciliation_cases WHERE id = :id"
    ), {"id": case_id}).mappings().one_or_none()
    if row is None:
        raise ReconciliationError("CASE_NOT_FOUND")
    return dict(row)


def resolve_case(db: Session, *, case_id: str, base_revision: int, resolution: str,
                 evidence: str | None = None, actor_id: str | None = None) -> dict:
    """CAS the open case to resolved_succeeded/resolved_failed (terminal) or
    resolved_retry (resume with a new dispatch generation)."""
    row = db.execute(text(
        "SELECT c.id, c.turn_id, c.revision, c.state FROM agent_reconciliation_cases c "
        "WHERE c.id = :id FOR UPDATE"
    ), {"id": case_id}).mappings().one_or_none()
    if row is None:
        db.rollback()
        raise ReconciliationError("CASE_NOT_FOUND")
    if row["state"] != "open":
        db.rollback()
        raise ReconciliationError("CASE_ALREADY_RESOLVED")
    if row["revision"] != base_revision:
        db.rollback()
        raise ReconciliationError("RECONCILIATION_CONFLICT")
    new_state = {"succeeded": "resolved_succeeded", "failed": "resolved_failed",
                 "retry": "resolved_retry"}.get(resolution)
    if new_state is None:
        db.rollback()
        raise ReconciliationError("RESOLUTION_INVALID")
    db.execute(text(
        "UPDATE agent_reconciliation_cases SET state = :state, revision = revision + 1, "
        "evidence_hash = :eh, resolver_id = :actor, updated_at = now() "
        "WHERE id = :id AND state = 'open' AND revision = :rev"
    ), {"state": new_state, "id": case_id, "rev": base_revision,
        "eh": _new_id(), "actor": actor_id})
    if resolution == "retry":
        # operator-confirmed not-run: resume the Turn with a fresh generation
        new_generation = db.execute(text(
            "UPDATE agent_turns SET status = 'queued', dispatch_generation = dispatch_generation + 1, "
            "updated_at = now() WHERE id = :id RETURNING dispatch_generation"
        ), {"id": row["turn_id"]}).scalar_one_or_none()
        if new_generation is not None:
            db.execute(text(
                "INSERT INTO agent_turn_dispatch_outbox (id, turn_id, dispatch_generation, "
                "operation, state, created_at) "
                "VALUES (:id, :turn, :gen, 'delivery_recovery', 'pending', now())"
            ), {"id": _new_id(), "turn": row["turn_id"], "gen": new_generation})
    else:
        db.execute(text(
            "UPDATE agent_turns SET status = 'failed', error_code = 'RECONCILIATION_' || :res, "
            "updated_at = now() WHERE id = :id"
        ), {"id": row["turn_id"], "res": resolution.upper()})
    db.commit()
    return {"case_id": case_id, "state": new_state, "resolution": resolution,
            "resumed": resolution == "retry"}
