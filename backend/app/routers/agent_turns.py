"""Agent session/Turn command API (P3A-TURNAPI).

Session/Turn create/status/cancel and single-use stream-ticket commands with
typed envelopes.  Turn creation returns 202 only after the authoritative
state plus the transactional dispatch outbox commit; cancel delegates to the
dispatch service; stream tickets are single-use 60-second secrets returned
once.  No graph execution or background fallback.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.agent_runtime import (
    CancelTurnRequest,
    CreateSessionRequest,
    CreateTurnRequest,
    StreamTicketResponse,
    TurnAcceptedResponse,
    TurnStatusResponse,
)
from app.services.runtime import turns

router = APIRouter()


def _require_agent_run_grant(db: Session, user_id: str, agent_id: str) -> None:
    """Existence-hiding: the caller must hold an active grant with the run
    capability, otherwise 404 (the agent may as well not exist)."""
    grant = db.execute(text(
        "SELECT 1 FROM agent_access_grants WHERE agent_id = :id AND user_id = :uid "
        "AND status = 'active' AND capabilities::text LIKE :cap LIMIT 1"
    ), {"id": agent_id, "uid": user_id, "cap": '%"run"%'}).scalar_one_or_none()
    if not grant:
        raise HTTPException(404, detail="Not found")


def _agent_id_for_session(db: Session, session_id: str) -> str:
    row = db.execute(text("SELECT agent_id FROM agent_sessions WHERE id = :id"),
                     {"id": session_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, detail="Not found")
    return row["agent_id"]


def _turn_agent_id(db: Session, turn_id: str) -> str:
    row = db.execute(text(
        "SELECT s.agent_id FROM agent_turns t JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE t.id = :id"
    ), {"id": turn_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, detail="Not found")
    return row["agent_id"]


@router.get("/agents/{agent_id}/sessions")
def list_sessions(agent_id: str, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user),
                  limit: int = Query(50, ge=1, le=100)):
    _require_agent_run_grant(db, current_user.id, agent_id)
    return {"data": turns.list_sessions(db, agent_id=agent_id, actor_id=current_user.id, limit=limit)}


@router.post("/agents/{agent_id}/sessions", status_code=201)
def create_session(agent_id: str, body: CreateSessionRequest, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user),
                   x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    _require_agent_run_grant(db, current_user.id, agent_id)
    if x_idempotency_key is not None and not (16 <= len(x_idempotency_key) <= 128
                                              and x_idempotency_key.isprintable()):
        raise HTTPException(422, detail="Idempotency-Key must be 16-128 printable ASCII characters")
    result = turns.create_session(db, agent_id=agent_id, actor_id=current_user.id, title=body.title)
    return {"data": result}


@router.get("/agent-sessions/{session_id}")
def get_session(session_id: str, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    agent_id = _agent_id_for_session(db, session_id)
    _require_agent_run_grant(db, current_user.id, agent_id)
    try:
        result = turns.get_session(db, session_id=session_id, actor_id=current_user.id)
    except turns.TurnApiError:
        raise HTTPException(404, detail="Not found")
    return {"data": result}


@router.delete("/agent-sessions/{session_id}", status_code=204)
def close_session(session_id: str, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    agent_id = _agent_id_for_session(db, session_id)
    _require_agent_run_grant(db, current_user.id, agent_id)
    try:
        turns.close_session(db, session_id=session_id, actor_id=current_user.id)
    except turns.TurnApiError:
        raise HTTPException(404, detail="Not found")
    return None


@router.get("/agent-sessions/{session_id}/messages")
def list_messages(session_id: str, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user),
                  limit: int = Query(50, ge=1, le=100),
                  after_ordinal: int | None = Query(None)):
    agent_id = _agent_id_for_session(db, session_id)
    _require_agent_run_grant(db, current_user.id, agent_id)
    try:
        result = turns.list_messages(db, session_id=session_id, actor_id=current_user.id,
                                     limit=limit, after_ordinal=after_ordinal)
    except turns.TurnApiError:
        raise HTTPException(404, detail="Not found")
    return {"data": result}


@router.post("/agent-sessions/{session_id}/turns", status_code=202)
def create_turn(session_id: str, body: CreateTurnRequest, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user),
                x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if x_idempotency_key is not None and not (16 <= len(x_idempotency_key) <= 128
                                              and x_idempotency_key.isprintable()):
        raise HTTPException(422, detail="Idempotency-Key must be 16-128 printable ASCII characters")
    agent_id = _agent_id_for_session(db, session_id)
    _require_agent_run_grant(db, current_user.id, agent_id)
    try:
        result = turns.create_turn(db, session_id=session_id, actor_id=current_user.id,
                                   user_message=body.user_message, turn_id=body.turn_id)
    except turns.TurnApiError as exc:
        code = str(exc)
        if code in ("SESSION_NOT_FOUND", "SESSION_NOT_ACTIVE"):
            raise HTTPException(404 if code == "SESSION_NOT_FOUND" else 423, detail=code)
        raise HTTPException(409, detail=code)
    return {"data": TurnAcceptedResponse(**result).model_dump()}


@router.get("/agent-turns/{turn_id}")
def get_turn(turn_id: str, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)):
    agent_id = _turn_agent_id(db, turn_id)
    _require_agent_run_grant(db, current_user.id, agent_id)
    try:
        result = turns.get_turn(db, turn_id=turn_id, actor_id=current_user.id)
    except turns.TurnApiError:
        raise HTTPException(404, detail="Not found")
    return {"data": TurnStatusResponse(**result).model_dump()}


@router.post("/agent-turns/{turn_id}/cancel", status_code=202)
def cancel_turn(turn_id: str, body: CancelTurnRequest, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user),
                x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if x_idempotency_key is not None and not (16 <= len(x_idempotency_key) <= 128
                                              and x_idempotency_key.isprintable()):
        raise HTTPException(422, detail="Idempotency-Key must be 16-128 printable ASCII characters")
    agent_id = _turn_agent_id(db, turn_id)
    _require_agent_run_grant(db, current_user.id, agent_id)
    try:
        result = turns.cancel_turn_api(db, turn_id=turn_id, actor_id=current_user.id)
    except turns.TurnApiError as exc:
        if str(exc) == "TURN_NOT_FOUND":
            raise HTTPException(404, detail="Not found")
        raise HTTPException(409, detail=str(exc))
    return {"data": TurnAcceptedResponse(**result).model_dump()}


@router.post("/agent-turns/{turn_id}/stream-ticket", status_code=201)
def stream_ticket(turn_id: str, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user),
                  x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if x_idempotency_key is not None and not (16 <= len(x_idempotency_key) <= 128
                                              and x_idempotency_key.isprintable()):
        raise HTTPException(422, detail="Idempotency-Key must be 16-128 printable ASCII characters")
    agent_id = _turn_agent_id(db, turn_id)
    _require_agent_run_grant(db, current_user.id, agent_id)
    try:
        result = turns.mint_stream_ticket(db, turn_id=turn_id, actor_id=current_user.id)
    except turns.TurnApiError:
        raise HTTPException(404, detail="Not found")
    return {"data": StreamTicketResponse(**result).model_dump()}
