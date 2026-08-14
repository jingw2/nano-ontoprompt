"""Agent runtime event API (P4A-STREAM).

`GET /agent-turns/{turn_id}/events?after_seq&limit` -> CursorPage of
persisted events; `GET /agent-turns/{turn_id}/stream?after_seq` -> SSE
`text/event-stream` with gap/terminal fallback.  Session owner + Agent run
grant; stream additionally requires a single-use bearer ticket verified
against the stored token hash.
"""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.user import User
from app.services.runtime import events

router = APIRouter()


class RuntimeEventResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    id: str
    turn_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: object | None = None


def _turn_agent_id(db: Session, turn_id: str) -> str:
    row = db.execute(text(
        "SELECT s.agent_id FROM agent_turns t JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE t.id = :id"
    ), {"id": turn_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, detail="Not found")
    return row["agent_id"]


def _require_run_grant(db: Session, user_id: str, agent_id: str) -> None:
    grant = db.execute(text(
        "SELECT 1 FROM agent_access_grants WHERE agent_id = :id AND user_id = :uid "
        "AND status = 'active' AND capabilities::text LIKE :cap LIMIT 1"
    ), {"id": agent_id, "uid": user_id, "cap": '%"run"%'}).scalar_one_or_none()
    if not grant:
        raise HTTPException(404, detail="Not found")


def _verify_ticket(db: Session, user_id: str, turn_id: str, ticket: str) -> bool:
    token_hash = hashlib.sha256(ticket.encode("utf-8")).hexdigest()
    row = db.execute(text(
        "SELECT id FROM agent_stream_tickets WHERE turn_id = :turn AND actor_user_id = :actor "
        "AND token_hash = :hash AND status = 'unused' AND expires_at > now() LIMIT 1"
    ), {"turn": turn_id, "actor": user_id, "hash": token_hash}).mappings().one_or_none()
    if row is None:
        return False
    # single-use: consume on first verified use
    db.execute(text(
        "UPDATE agent_stream_tickets SET status = 'consumed', consumed_at = now() "
        "WHERE id = :id AND status = 'unused'"
    ), {"id": row["id"]})
    db.commit()
    return True


@router.get("/agent-turns/{turn_id}/events")
def turn_events(turn_id: str, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user),
                after_seq: int | None = Query(None), limit: int = Query(100, ge=1, le=500)):
    agent_id = _turn_agent_id(db, turn_id)
    _require_run_grant(db, current_user.id, agent_id)
    result = events.list_events(db, turn_id=turn_id, after_seq=after_seq, limit=limit)
    return {"data": result}


@router.get("/agent-turns/{turn_id}/stream")
def turn_stream(turn_id: str, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user),
                ticket: str = Query(...), after_seq: int | None = Query(None)):
    agent_id = _turn_agent_id(db, turn_id)
    _require_run_grant(db, current_user.id, agent_id)
    if not _verify_ticket(db, current_user.id, turn_id, ticket):
        raise HTTPException(401, detail="STREAM_TICKET_INVALID")
    chunks = events.stream_chunk(db, turn_id=turn_id, after_seq=after_seq)

    def sse_body():
        for chunk in chunks:
            yield f"event: {chunk['event']}\n"
            import json as _json
            yield f"data: {_json.dumps(chunk.get('data', {}), ensure_ascii=False)}\n\n"
        # terminal fallback: end the stream after a terminal event
        if chunks and chunks[-1]["event"] == "terminal":
            yield "event: done\n\n"

    return StreamingResponse(sse_body(), media_type="text/event-stream")
