"""Agent reconciliation API (P5C-EXECUTE).

Admin-only, cursor-paged list/detail and idempotent resolve for unknown
outcome cases.  Unresolved uncertainty cannot select replay; a stale base
revision conflicts; wrong-actor/unauthorized requests are denied.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.user import User
from app.services.runtime.reconciliation import (
    ReconciliationError,
    get_case,
    list_cases,
    resolve_case,
)

router = APIRouter()


class ResolveReconciliationRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    base_revision: int
    resolution: str  # succeeded | failed | retry
    evidence: str | None = None


class ReconciliationCaseOut(BaseModel):
    model_config = {"protected_namespaces": ()}
    id: str
    turn_id: str
    execution_kind: str
    execution_id: str
    revision: int
    state: str
    request_hash: str | None = None


@router.get("/admin/agent-reconciliations")
def reconciliation_list(db: Session = Depends(get_db),
                        current_user: User = Depends(require_admin),
                        status: str | None = Query(None), limit: int = Query(50, ge=1, le=100)):
    result = list_cases(db, status=status, limit=limit)
    return {"data": result}


@router.get("/admin/agent-reconciliations/{case_id}")
def reconciliation_detail(case_id: str, db: Session = Depends(get_db),
                          current_user: User = Depends(require_admin)):
    try:
        result = get_case(db, case_id=case_id)
    except ReconciliationError as exc:
        if str(exc) == "CASE_NOT_FOUND":
            raise HTTPException(404, detail="Not found")
        raise HTTPException(409, detail=str(exc))
    return {"data": ReconciliationCaseOut(**result).model_dump()}


@router.post("/admin/agent-reconciliations/{case_id}/resolve")
def reconciliation_resolve(case_id: str, body: ResolveReconciliationRequest,
                           db: Session = Depends(get_db),
                           current_user: User = Depends(require_admin)):
    try:
        result = resolve_case(db, case_id=case_id, base_revision=body.base_revision,
                              resolution=body.resolution, evidence=body.evidence,
                              actor_id=current_user.id)
    except ReconciliationError as exc:
        code = str(exc)
        if code == "CASE_NOT_FOUND":
            raise HTTPException(404, detail="Not found")
        if code == "RECONCILIATION_CONFLICT":
            raise HTTPException(409, detail=code)
        raise HTTPException(409, detail=code)
    return {"data": result}
