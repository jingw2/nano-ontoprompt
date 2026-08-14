"""Durable Agent Turn commands (P3A-TURNAPI).

Session/Turn create/status/cancel and single-use stream-ticket receipts.
Turn creation returns 202 only after the authoritative state plus the
transactional dispatch outbox row are committed; a repeated `turn_id` replays
the stable Turn without another message, Turn, or dispatch row.  There is no
graph execution or background fallback here.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.runtime.dispatch import DispatchError, cancel_turn


class TurnApiError(Exception):
    """Rejected Turn command."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _correlation(operation: str, turn_id: str) -> str:
    return f"turn:{operation}:{turn_id}"


def create_session(db: Session, *, agent_id: str, actor_id: str, title: str | None = None) -> dict:
    """Create a session owned by the actor for the agent."""
    session_id = _new_id()
    db.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES (:id, :agent, :owner, 'active', now(), now())"
    ), {"id": session_id, "agent": agent_id, "owner": actor_id})
    db.commit()
    return _session_out(db, session_id)


def _session_out(db: Session, session_id: str) -> dict:
    row = db.execute(text(
        "SELECT id, agent_id, owner_user_id, status, active_turn_id, created_at, updated_at "
        "FROM agent_sessions WHERE id = :id"
    ), {"id": session_id}).mappings().one()
    return dict(row)


def list_sessions(db: Session, *, agent_id: str, actor_id: str, limit: int = 50) -> dict:
    rows = db.execute(text(
        "SELECT id, agent_id, owner_user_id, status, active_turn_id, created_at, updated_at "
        "FROM agent_sessions WHERE agent_id = :agent AND owner_user_id = :owner "
        "ORDER BY updated_at DESC LIMIT :limit"
    ), {"agent": agent_id, "owner": actor_id, "limit": limit}).mappings().all()
    return {"items": [dict(r) for r in rows], "next_cursor": None, "has_more": False}


def get_session(db: Session, *, session_id: str, actor_id: str) -> dict:
    row = db.execute(text(
        "SELECT id, agent_id, owner_user_id, status, active_turn_id, created_at, updated_at "
        "FROM agent_sessions WHERE id = :id"
    ), {"id": session_id}).mappings().one_or_none()
    if row is None or row["owner_user_id"] != actor_id:
        raise TurnApiError("SESSION_NOT_FOUND")
    return dict(row)


def close_session(db: Session, *, session_id: str, actor_id: str) -> None:
    row = db.execute(text(
        "SELECT id, owner_user_id, status FROM agent_sessions WHERE id = :id"
    ), {"id": session_id}).mappings().one_or_none()
    if row is None or row["owner_user_id"] != actor_id:
        raise TurnApiError("SESSION_NOT_FOUND")
    if row["status"] == "closed":
        db.rollback()
        return
    if row["status"] != "active":
        raise TurnApiError("SESSION_TERMINAL")
    db.execute(text(
        "UPDATE agent_sessions SET status = 'closed', updated_at = now() WHERE id = :id"
    ), {"id": session_id})
    db.commit()


def list_messages(db: Session, *, session_id: str, actor_id: str, limit: int = 50,
                  after_ordinal: int | None = None) -> dict:
    row = db.execute(text(
        "SELECT owner_user_id FROM agent_sessions WHERE id = :id"
    ), {"id": session_id}).mappings().one_or_none()
    if row is None or row["owner_user_id"] != actor_id:
        raise TurnApiError("SESSION_NOT_FOUND")
    params: dict = {"session_id": session_id, "limit": limit}
    after_clause = ""
    if after_ordinal is not None:
        after_clause = "AND ordinal > :after"
        params["after"] = after_ordinal
    rows = db.execute(text(
        "SELECT id, session_id, turn_id, role, ordinal, content, created_at "
        "FROM agent_messages WHERE session_id = :session_id " + after_clause + " "
        "ORDER BY ordinal LIMIT :limit"
    ), params).mappings().all()
    has_more = len(rows) > limit
    return {"items": [dict(r) for r in rows[:limit]], "next_cursor": None, "has_more": has_more}


def create_turn(
    db: Session, *, session_id: str, actor_id: str, user_message: str,
    turn_id: str | None = None,
) -> dict:
    """Lock the session, insert queued Turn + user message + request FK +
    active pointer + dispatch outbox, and commit — 202 only after commit.
    A repeated `turn_id` replays the stored Turn (no duplicate rows)."""
    session = db.execute(text(
        "SELECT id, owner_user_id, status FROM agent_sessions WHERE id = :id FOR UPDATE"
    ), {"id": session_id}).mappings().one_or_none()
    if session is None or session["owner_user_id"] != actor_id:
        db.rollback()
        raise TurnApiError("SESSION_NOT_FOUND")
    if session["status"] != "active":
        db.rollback()
        raise TurnApiError("SESSION_NOT_ACTIVE")

    # idempotent replay: a stable client turn_id returns the stored Turn
    if turn_id:
        existing = db.execute(text(
            "SELECT id, session_id, status, dispatch_generation FROM agent_turns "
            "WHERE id = :id AND session_id = :sid"
        ), {"id": turn_id, "sid": session_id}).mappings().one_or_none()
        if existing is not None:
            db.commit()
            return {
                "turn_id": existing["id"], "session_id": existing["session_id"],
                "status": existing["status"], "dispatch_generation": existing["dispatch_generation"],
                "replayed": True, "correlation_id": _correlation("create", existing["id"]),
                "stream_ticket": None, "stream_ticket_url": None,
            }

    final_turn_id = turn_id or _new_id()
    message_id = _new_id()
    ordinal = db.execute(text(
        "SELECT coalesce(max(ordinal), 0) + 1 FROM agent_messages WHERE session_id = :sid"
    ), {"sid": session_id}).scalar_one()
    try:
        db.execute(text(
            "INSERT INTO agent_turns (id, session_id, status, dispatch_generation, created_at, updated_at) "
            "VALUES (:id, :sid, 'queued', 1, now(), now())"
        ), {"id": final_turn_id, "sid": session_id})
        db.execute(text(
            "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
            "VALUES (:id, :sid, :turn, 'user', :ord, :content, now())"
        ), {"id": message_id, "sid": session_id, "turn": final_turn_id,
            "ord": ordinal, "content": user_message})
        db.execute(text(
            "UPDATE agent_turns SET request_message_id = :mid, updated_at = now() WHERE id = :id"
        ), {"mid": message_id, "id": final_turn_id})
        db.execute(text(
            "UPDATE agent_sessions SET active_turn_id = :turn, updated_at = now() "
            "WHERE id = :sid"
        ), {"turn": final_turn_id, "sid": session_id})
        db.execute(text(
            "INSERT INTO agent_turn_dispatch_outbox (id, turn_id, dispatch_generation, operation, state, created_at) "
            "VALUES (:id, :turn, 1, 'turn', 'pending', now())"
        ), {"id": _new_id(), "turn": final_turn_id})
    except Exception:
        db.rollback()
        raise TurnApiError("TURN_CREATE_CONFLICT") from None
    db.commit()
    return {
        "turn_id": final_turn_id, "session_id": session_id, "status": "queued",
        "dispatch_generation": 1, "replayed": False,
        "correlation_id": _correlation("create", final_turn_id),
        "stream_ticket": None, "stream_ticket_url": None,
    }


def get_turn(db: Session, *, turn_id: str, actor_id: str) -> dict:
    row = db.execute(text(
        "SELECT t.id, t.session_id, t.status, t.dispatch_generation, t.request_message_id, "
        "t.response_message_id, t.error_code, t.created_at, t.updated_at, s.owner_user_id "
        "FROM agent_turns t JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE t.id = :id"
    ), {"id": turn_id}).mappings().one_or_none()
    if row is None or row["owner_user_id"] != actor_id:
        raise TurnApiError("TURN_NOT_FOUND")
    return dict(row) | {"turn_id": row["id"]}


def cancel_turn_api(db: Session, *, turn_id: str, actor_id: str) -> dict:
    """Pre-claim cancel -> terminal cancelled; claimed -> cancelling signal.
    Delegates to the dispatch service (Section 6 semantics)."""
    row = db.execute(text(
        "SELECT s.owner_user_id, s.id AS session_id FROM agent_turns t "
        "JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE t.id = :id"
    ), {"id": turn_id}).mappings().one_or_none()
    if row is None or row["owner_user_id"] != actor_id:
        raise TurnApiError("TURN_NOT_FOUND")
    try:
        result = cancel_turn(db, turn_id=turn_id, actor_id=actor_id)
    except DispatchError as exc:
        raise TurnApiError(str(exc))
    return {
        "turn_id": turn_id, "session_id": row["session_id"], "status": result["status"],
        "correlation_id": _correlation("cancel", turn_id),
        "stream_ticket": None, "stream_ticket_url": None,
    }


def mint_stream_ticket(db: Session, *, turn_id: str, actor_id: str, ttl_seconds: int = 60) -> dict:
    """Mint a fresh single-use stream ticket, atomically revoking any prior
    unconsumed ticket for this actor/turn.  Secret is returned once."""
    row = db.execute(text(
        "SELECT s.owner_user_id FROM agent_turns t JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE t.id = :id"
    ), {"id": turn_id}).mappings().one_or_none()
    if row is None or row["owner_user_id"] != actor_id:
        raise TurnApiError("TURN_NOT_FOUND")
    now = _now()
    expires = now + timedelta(seconds=ttl_seconds)
    ticket = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(ticket.encode("utf-8")).hexdigest()
    db.execute(text(
        "UPDATE agent_stream_tickets SET status = 'revoked', consumed_at = now() "
        "WHERE turn_id = :turn AND actor_user_id = :actor AND status = 'unused'"
    ), {"turn": turn_id, "actor": actor_id})
    db.execute(text(
        "INSERT INTO agent_stream_tickets (id, turn_id, actor_user_id, token_hash, status, expires_at, created_at) "
        "VALUES (:id, :turn, :actor, :hash, 'unused', :expires, now())"
    ), {"id": _new_id(), "turn": turn_id, "actor": actor_id,
        "hash": token_hash, "expires": expires})
    db.commit()
    return {
        "turn_id": turn_id, "ticket": ticket, "expires_at": expires,
        "stream_ticket_url": f"/api/v1/agent-turns/{turn_id}/stream?ticket={ticket}",
    }
