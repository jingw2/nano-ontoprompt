"""Worker finalization (P4A-WORKER).

One fenced transaction that makes a claimed Turn terminal: the assistant
response message is recorded, the Turn CASes `running -> succeeded` under the
exact claim token/generation and a live lease, the session active pointer is
released, and every unresolved dispatch outbox row for the Turn is resolved.
A stale fence (0 affected rows) fails closed with `TURN_FENCE_LOST` and rolls
back — the sweeper then requeues the Turn.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


class TurnFinalizeError(Exception):
    """Rejected finalization (fail closed)."""


def _new_id() -> str:
    return str(uuid.uuid4())


def record_assistant_message(db: Session, *, session_id: str, turn_id: str, content: str) -> str:
    """Insert the assistant response message at the next session ordinal and
    return its id (same raw-SQL pattern as `turns.create_turn`)."""
    ordinal = db.execute(text(
        "SELECT coalesce(max(ordinal), 0) + 1 FROM agent_messages WHERE session_id = :sid"
    ), {"sid": session_id}).scalar_one()
    message_id = _new_id()
    db.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES (:id, :sid, :turn, 'assistant', :ord, :content, now())"
    ), {"id": message_id, "sid": session_id, "turn": turn_id, "ord": ordinal,
        "content": content})
    return message_id


def finalize_turn_succeeded(db: Session, *, turn_id: str, session_id: str,
                            claim_generation: int, claim_token: str,
                            response_message_id: str) -> dict:
    """CAS the claimed Turn to `succeeded` under the live fence (claim
    generation + token + unexpired lease); zero affected rows raises
    `TURN_FENCE_LOST` and rolls back the whole finalization transaction."""
    result = db.execute(text(
        "UPDATE agent_turns SET status = 'succeeded', response_message_id = :mid, "
        "updated_at = now() "
        "WHERE id = :id AND status = 'running' AND claim_generation = :gen "
        "AND claim_token = :token AND lease_expires_at > now()"
    ), {"id": turn_id, "mid": response_message_id, "gen": claim_generation,
        "token": claim_token})
    if result.rowcount != 1:
        db.rollback()
        raise TurnFinalizeError("TURN_FENCE_LOST")
    db.execute(text(
        "UPDATE agent_sessions SET active_turn_id = NULL, updated_at = now() "
        "WHERE id = :sid AND active_turn_id = :tid"
    ), {"sid": session_id, "tid": turn_id})
    db.execute(text(
        "UPDATE agent_turn_dispatch_outbox SET state = 'resolved_terminal', "
        "resolved_at = now(), resolution = 'terminal' "
        "WHERE turn_id = :tid AND state IN ('claimed', 'delivered', 'delivered_unclaimed')"
    ), {"tid": turn_id})
    return {"turn_id": turn_id, "status": "succeeded",
            "response_message_id": response_message_id}
