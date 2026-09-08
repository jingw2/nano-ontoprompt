"""Agent clarification API (P3B-INTERRUPT).

`GET /agent-clarifications/{id}` (200, ClarificationResponse) and
`POST /agent-clarifications/{id}/answer` (202, AnswerClarificationRequest ->
TurnAcceptedResponse).  One transaction CASes the pending clarification,
writes the user message, and inserts the resume dispatch outbox; a second
answer returns CLARIFICATION_ALREADY_RESOLVED, a changed base revision
returns CLARIFICATION_STALE.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.user import User
from app.services.runtime.clarification import (
    ClarificationError,
    answer_clarification,
    get_clarification,
)

router = APIRouter()


class ClarificationResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    id: str
    turn_id: str
    question: str
    answer: str | None = None
    base_request_revision: int
    status: str
    created_at: object | None = None
    answered_at: object | None = None


class AnswerClarificationRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    base_request_revision: int
    answer: str = Field(..., min_length=1, max_length=100_000)


class TurnAcceptedResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    turn_id: str
    session_id: str
    status: str
    dispatch_generation: int
    correlation_id: str
    stream_ticket: str | None = None
    stream_ticket_url: str | None = None


def _agent_id_for_clarification(db: Session, clarification_id: str) -> str:
    row = db.execute(text(
        "SELECT s.agent_id FROM agent_clarification_requests c "
        "JOIN agent_turns t ON t.id = c.turn_id "
        "JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE c.id = :id"
    ), {"id": clarification_id}).mappings().one_or_none()
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


@router.get("/agent-clarifications/{clarification_id}")
def clarification_detail(clarification_id: str, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    agent_id = _agent_id_for_clarification(db, clarification_id)
    _require_run_grant(db, current_user.id, agent_id)
    try:
        row = get_clarification(db, clarification_id=clarification_id, actor_id=current_user.id)
    except ClarificationError:
        raise HTTPException(404, detail="Not found")
    return {"data": ClarificationResponse(**row).model_dump()}


@router.post("/agent-clarifications/{clarification_id}/answer", status_code=202)
def clarification_answer(clarification_id: str, body: AnswerClarificationRequest,
                         db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    agent_id = _agent_id_for_clarification(db, clarification_id)
    _require_run_grant(db, current_user.id, agent_id)
    try:
        result = answer_clarification(
            db, clarification_id=clarification_id, actor_id=current_user.id,
            base_request_revision=body.base_request_revision, answer=body.answer,
        )
    except ClarificationError as exc:
        code = str(exc)
        if code == "CLARIFICATION_NOT_FOUND":
            raise HTTPException(404, detail="Not found")
        if code == "CLARIFICATION_STALE":
            raise HTTPException(409, detail="CLARIFICATION_STALE")
        raise HTTPException(409, detail=code)
    return {"data": TurnAcceptedResponse(**result).model_dump()}
