"""Agent application-state + audit API (P3B-STATEAUDIT).

Schema-validated snapshot/patch with CAS, and read-only scoped audit
list/detail envelopes.  No audit write route exists.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.agent_application_state import (
    ApplicationStateResponse,
    AuditEventResponse,
    PatchApplicationStateRequest,
)
from app.services.agent_audit_query import get_audit, list_audit
from app.services.runtime.application_state import (
    ApplicationStateError,
    get_snapshot,
    patch_snapshot,
)

router = APIRouter()


def _require_run_grant(db: Session, user_id: str, agent_id: str) -> None:
    grant = db.execute(text(
        "SELECT 1 FROM agent_access_grants WHERE agent_id = :id AND user_id = :uid "
        "AND status = 'active' AND capabilities::text LIKE :cap LIMIT 1"
    ), {"id": agent_id, "uid": user_id, "cap": '%"run"%'}).scalar_one_or_none()
    if not grant:
        raise HTTPException(404, detail="Not found")


def _require_view_audit(db: Session, user_id: str, agent_id: str) -> None:
    grant = db.execute(text(
        "SELECT 1 FROM agent_access_grants WHERE agent_id = :id AND user_id = :uid "
        "AND status = 'active' AND capabilities::text LIKE :cap LIMIT 1"
    ), {"id": agent_id, "uid": user_id, "cap": '%"view_audit"%'}).scalar_one_or_none()
    if not grant:
        raise HTTPException(404, detail="Not found")


def _agent_id_for_session(db: Session, session_id: str) -> str:
    row = db.execute(text("SELECT agent_id FROM agent_sessions WHERE id = :id"),
                     {"id": session_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, detail="Not found")
    return row["agent_id"]


@router.get("/agent-sessions/{session_id}/application-state")
def application_state_get(session_id: str, db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    agent_id = _agent_id_for_session(db, session_id)
    _require_run_grant(db, current_user.id, agent_id)
    try:
        result = get_snapshot(db, session_id=session_id)
    except ApplicationStateError as exc:
        raise HTTPException(404, detail=str(exc))
    return {"data": ApplicationStateResponse(**result).model_dump()}


@router.post("/agent-sessions/{session_id}/application-state", status_code=201)
def application_state_patch(session_id: str, body: PatchApplicationStateRequest,
                            db: Session = Depends(get_db),
                            current_user: User = Depends(get_current_user)):
    agent_id = _agent_id_for_session(db, session_id)
    _require_run_grant(db, current_user.id, agent_id)
    try:
        result = patch_snapshot(db, session_id=session_id, actor_id=current_user.id,
                                base_revision=body.base_revision, base_hash=body.base_hash,
                                patch=body.patch)
    except ApplicationStateError as exc:
        code = str(exc)
        if code == "APPLICATION_STATE_CONFLICT":
            raise HTTPException(409, detail=code)
        raise HTTPException(422, detail=code)
    return {"data": ApplicationStateResponse(**result).model_dump()}


@router.get("/agents/{agent_id}/audit")
def agent_audit_list(agent_id: str, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user),
                     limit: int = Query(50, ge=1, le=100)):
    _require_view_audit(db, current_user.id, agent_id)
    result = list_audit(db, security_domain_id=current_user.security_domain_id,
                        agent_id=agent_id, limit=limit)
    return {"data": result}


@router.get("/agent-turns/{turn_id}/audit")
def turn_audit_list(turn_id: str, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user),
                    limit: int = Query(50, ge=1, le=100)):
    row = db.execute(text(
        "SELECT s.agent_id FROM agent_turns t JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE t.id = :id"
    ), {"id": turn_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, detail="Not found")
    _require_view_audit(db, current_user.id, row["agent_id"])
    result = list_audit(db, security_domain_id=current_user.security_domain_id,
                        turn_id=turn_id, limit=limit)
    return {"data": result}
