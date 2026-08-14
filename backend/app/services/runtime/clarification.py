"""Agent clarification interrupts (P3B-INTERRUPT, Section 6.1).

`request_clarification` persists a ClarificationRequest before interrupting;
the answer endpoint CASes `(pending, base_request_revision) -> answered`, writes
a user clarification message, and inserts a unique resume dispatch outbox in
the same transaction.  A second answer returns CLARIFICATION_ALREADY_RESOLVED;
a changed base revision returns CLARIFICATION_STALE.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


class ClarificationError(Exception):
    """Rejected clarification operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _correlation(operation: str, clarification_id: str) -> str:
    return f"clarification:{operation}:{clarification_id}"


def create_clarification(
    db: Session, *, turn_id: str, question: str, reason_code: str = "ambiguous",
) -> dict:
    """Persist a ClarificationRequest and CAS the Turn to
    awaiting_clarification; one transaction (the worker owns this path)."""
    turn = db.execute(text(
        "SELECT id, status FROM agent_turns WHERE id = :id FOR UPDATE"
    ), {"id": turn_id}).mappings().one_or_none()
    if turn is None:
        db.rollback()
        raise ClarificationError("TURN_NOT_FOUND")
    if turn["status"] not in ("running", "queued"):
        db.rollback()
        raise ClarificationError("TURN_NOT_INTERRUPTIBLE")
    clarification_id = _new_id()
    db.execute(text(
        "INSERT INTO agent_clarification_requests (id, turn_id, question, status, created_at) "
        "VALUES (:id, :turn, :q, 'pending', now())"
    ), {"id": clarification_id, "turn": turn_id, "q": question})
    db.execute(text(
        "UPDATE agent_turns SET status = 'awaiting_clarification', updated_at = now() "
        "WHERE id = :id"
    ), {"id": turn_id})
    db.commit()
    return {
        "id": clarification_id, "turn_id": turn_id, "question": question,
        "status": "pending", "base_request_revision": 1,
        "correlation_id": _correlation("create", clarification_id),
    }


def get_clarification(db: Session, *, clarification_id: str, actor_id: str) -> dict:
    row = db.execute(text(
        "SELECT c.id, c.turn_id, c.question, c.answer, c.base_request_revision, c.status, "
        "c.created_at, c.answered_at, s.owner_user_id "
        "FROM agent_clarification_requests c "
        "JOIN agent_turns t ON t.id = c.turn_id "
        "JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE c.id = :id"
    ), {"id": clarification_id}).mappings().one_or_none()
    if row is None or row["owner_user_id"] != actor_id:
        raise ClarificationError("CLARIFICATION_NOT_FOUND")
    return dict(row)


def answer_clarification(
    db: Session, *, clarification_id: str, actor_id: str, base_request_revision: int, answer: str,
) -> dict:
    """CAS `(pending, base_request_revision) -> answered`, write a user
    clarification message, and insert a unique resume dispatch outbox.  202
    only after the outbox commit."""
    row = db.execute(text(
        "SELECT c.id, c.turn_id, c.status, c.base_request_revision, t.session_id "
        "FROM agent_clarification_requests c "
        "JOIN agent_turns t ON t.id = c.turn_id "
        "WHERE c.id = :id FOR UPDATE"
    ), {"id": clarification_id}).mappings().one_or_none()
    if row is None:
        db.rollback()
        raise ClarificationError("CLARIFICATION_NOT_FOUND")
    session_owner = db.execute(text(
        "SELECT owner_user_id FROM agent_sessions WHERE id = :sid"
    ), {"sid": row["session_id"]}).scalar_one_or_none()
    if session_owner != actor_id:
        db.rollback()
        raise ClarificationError("CLARIFICATION_NOT_FOUND")
    if row["status"] != "pending":
        db.rollback()
        raise ClarificationError("CLARIFICATION_ALREADY_RESOLVED")
    if row["base_request_revision"] != base_request_revision:
        db.rollback()
        raise ClarificationError("CLARIFICATION_STALE")

    message_id = _new_id()
    ordinal = db.execute(text(
        "SELECT coalesce(max(ordinal), 0) + 1 FROM agent_messages WHERE session_id = :sid"
    ), {"sid": row["session_id"]}).scalar_one()
    # CAS the clarification to answered
    result = db.execute(text(
        "UPDATE agent_clarification_requests SET status = 'answered', answer = :a, "
        "answered_at = now() WHERE id = :id AND status = 'pending' "
        "AND base_request_revision = :rev"
    ), {"id": clarification_id, "a": answer, "rev": base_request_revision})
    if result.rowcount != 1:
        db.rollback()
        raise ClarificationError("CLARIFICATION_ALREADY_RESOLVED")
    # user clarification message
    db.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES (:id, :sid, :turn, 'user', :ord, :content, now())"
    ), {"id": message_id, "sid": row["session_id"], "turn": row["turn_id"],
        "ord": ordinal, "content": answer})
    # CAS turn back to queued with a fresh dispatch generation
    new_generation = db.execute(text(
        "UPDATE agent_turns SET status = 'queued', dispatch_generation = dispatch_generation + 1, "
        "updated_at = now() WHERE id = :id AND status = 'awaiting_clarification' "
        "RETURNING dispatch_generation"
    ), {"id": row["turn_id"]}).scalar_one_or_none()
    if new_generation is None:
        db.rollback()
        raise ClarificationError("TURN_NOT_INTERRUPTIBLE")
    db.execute(text(
        "INSERT INTO agent_turn_dispatch_outbox (id, turn_id, dispatch_generation, operation, state, created_at) "
        "VALUES (:id, :turn, :gen, 'resume_clarification', 'pending', now())"
    ), {"id": _new_id(), "turn": row["turn_id"], "gen": new_generation})
    db.commit()
    return {
        "turn_id": row["turn_id"], "session_id": row["session_id"],
        "status": "queued", "dispatch_generation": new_generation,
        "correlation_id": _correlation("answer", clarification_id),
        "stream_ticket": None, "stream_ticket_url": None,
    }
