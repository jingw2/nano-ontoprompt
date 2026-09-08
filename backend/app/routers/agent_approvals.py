"""Agent action approval API (P5B-APPROVAL).

`GET /agent-approvals/{id}` -> ApprovalResponse; `POST .../approve` and
`POST .../reject` -> ResolveApprovalRequest -> TurnAcceptedResponse (202) or
APPROVAL_STALE/APPROVAL_ALREADY_RESOLVED/APPROVAL_EXPIRED.  The authenticated
user must be exactly the designated actor; others get existence-hiding 404.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.user import User
from app.services.actions.approval import ApprovalError, get_approval, resolve_approval

router = APIRouter()


class ResolveApprovalRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    base_revision: int
    preview_hash: str


class ApprovalResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    id: str
    turn_id: str
    tool_execution_id: str
    designated_actor_id: str
    revision: int
    status: str
    preview_hash: str
    expires_at: object | None = None
    stale_reason: str | None = None


class TurnAcceptedResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    approval_id: str
    turn_id: str
    status: str
    dispatch_generation: int
    correlation_id: str
    stream_ticket: str | None = None
    stream_ticket_url: str | None = None


def _agent_id_for_approval(db: Session, approval_id: str) -> str:
    row = db.execute(text(
        "SELECT s.agent_id FROM agent_approvals a "
        "JOIN agent_turns t ON t.id = a.turn_id "
        "JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE a.id = :id"
    ), {"id": approval_id}).mappings().one_or_none()
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


def _resolve(approval_id: str, body: ResolveApprovalRequest, decision: str,
             db: Session, current_user: User) -> dict:
    agent_id = _agent_id_for_approval(db, approval_id)
    _require_run_grant(db, current_user.id, agent_id)
    try:
        result = resolve_approval(
            db, approval_id=approval_id, actor_id=current_user.id,
            base_revision=body.base_revision, preview_hash=body.preview_hash, decision=decision,
        )
    except ApprovalError as exc:
        code = str(exc)
        if code == "APPROVAL_NOT_FOUND":
            raise HTTPException(404, detail="Not found")
        if code == "APPROVAL_EXPIRED":
            raise HTTPException(410, detail="APPROVAL_EXPIRED")
        raise HTTPException(409, detail=code)
    return result


@router.get("/agent-approvals/{approval_id}")
def approval_detail(approval_id: str, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    agent_id = _agent_id_for_approval(db, approval_id)
    _require_run_grant(db, current_user.id, agent_id)
    try:
        row = get_approval(db, approval_id=approval_id, actor_id=current_user.id)
    except ApprovalError:
        raise HTTPException(404, detail="Not found")
    return {"data": ApprovalResponse(**row).model_dump()}


@router.post("/agent-approvals/{approval_id}/approve", status_code=202)
def approval_approve(approval_id: str, body: ResolveApprovalRequest,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    result = _resolve(approval_id, body, "approved", db, current_user)
    return {"data": TurnAcceptedResponse(**result).model_dump()}


@router.post("/agent-approvals/{approval_id}/reject", status_code=202)
def approval_reject(approval_id: str, body: ResolveApprovalRequest,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    result = _resolve(approval_id, body, "rejected", db, current_user)
    return {"data": TurnAcceptedResponse(**result).model_dump()}
