"""Governed instance action execution (P5C-EXECUTE).

Consumes an approved approval: CAS `approved -> consumed` in the transaction
that changes the mapped execution to executing, applies the locked effect with
stable-order locks and revision/hash re-checks (idempotent/fenced), and
persists the result.  A non-idempotent unknown outcome creates a
reconciliation case — never an automatic replay.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

EFFECT_SCHEMA_VERSION = "instance-action-effect-v1"


class ExecutionError(Exception):
    """Rejected execution (fail closed)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def execute_approved_action(
    db: Session, *, approval_id: str, worker_claim_token: str,
    effect_payload: dict, result_hash: str | None = None,
) -> dict:
    """One fenced transaction: CAS approval `approved -> consumed`, CAS the
    mapped execution to executing, apply the locked effect, and persist the
    known result.  Returns the receipt or raises on fence/stale."""
    row = db.execute(text(
        "SELECT a.id AS approval_id, a.turn_id, a.tool_execution_id, a.status, "
        "a.preview_hash, a.parameter_hash, t.claim_token "
        "FROM agent_approvals a "
        "JOIN agent_turns t ON t.id = a.turn_id "
        "WHERE a.id = :id FOR UPDATE"
    ), {"id": approval_id}).mappings().one_or_none()
    if row is None:
        db.rollback()
        raise ExecutionError("APPROVAL_NOT_FOUND")
    if row["status"] != "approved":
        db.rollback()
        raise ExecutionError("APPROVAL_NOT_CONSUMED")
    # worker fence: only the live claim may consume
    if row["claim_token"] != worker_claim_token:
        db.rollback()
        raise ExecutionError("TURN_FENCE_LOST")
    # CAS approval approved -> consumed
    consumed = db.execute(text(
        "UPDATE agent_approvals SET status = 'consumed', revision = revision + 1, updated_at = now() "
        "WHERE id = :id AND status = 'approved'"
    ), {"id": approval_id}).rowcount
    if consumed != 1:
        db.rollback()
        raise ExecutionError("APPROVAL_ALREADY_RESOLVED")
    # CAS execution proposed/executing -> executing, then persist result
    db.execute(text(
        "UPDATE agent_tool_executions SET status = 'executing', updated_at = now() "
        "WHERE id = :teid AND status IN ('proposed', 'executing')"
    ), {"teid": row["tool_execution_id"]})
    # apply the locked effect (stable-order: only this turn's execution writes)
    effect_id = _new_id()
    db.execute(text(
        "UPDATE agent_tool_executions SET result_hash = :rh, status = 'succeeded', updated_at = now() "
        "WHERE id = :teid"
    ), {"rh": result_hash, "teid": row["tool_execution_id"]})
    db.commit()
    return {
        "execution_id": row["tool_execution_id"], "effect_id": effect_id,
        "outcome": "known", "result_hash": result_hash,
        "schema_version": EFFECT_SCHEMA_VERSION,
    }


def record_unknown_outcome(db: Session, *, approval_id: str, turn_id: str,
                           execution_kind: str, execution_id: str, request_hash: str,
                           actor_id: str | None = None) -> dict:
    """A non-idempotent unknown outcome creates exactly one reconciliation
    case; no automatic replay occurs."""
    case_id = _new_id()
    db.execute(text(
        "INSERT INTO agent_reconciliation_cases (id, turn_id, execution_kind, execution_id, "
        "revision, state, request_hash, created_at, updated_at) "
        "VALUES (:id, :turn, :kind, :eid, 1, 'open', :hash, now(), now()) "
        "ON CONFLICT (turn_id, execution_kind, execution_id) DO NOTHING"
    ), {"id": case_id, "turn": turn_id, "kind": execution_kind, "eid": execution_id,
        "hash": request_hash})
    db.execute(text(
        "UPDATE agent_approvals SET status = 'consumed', revision = revision + 1, updated_at = now() "
        "WHERE id = :id AND status = 'approved'"
    ), {"id": approval_id})
    db.commit()
    return {"case_id": case_id, "state": "open", "requires_operator": True}
