"""Retention governance admin API (P6A). Admin-only (see Global Constraints
on why this plan uses require_admin rather than a new granular capability)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.user import User
from app.schemas.retention import (
    ActivateRetentionPolicyRequest,
    CreateRetentionHoldRequest,
    CreateRetentionPolicyRequest,
    ReleaseRetentionHoldRequest,
)
from app.services.retention.holds import RetentionHoldError, create_hold, release_hold
from app.services.retention.policy import (
    RetentionPolicyConflict,
    RetentionPolicyError,
    activate_policy_version,
    create_policy_version,
)

router = APIRouter()


def _policy_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RetentionPolicyConflict):
        return HTTPException(409, detail=str(exc))
    if isinstance(exc, RetentionPolicyError):
        return HTTPException(422, detail=str(exc))
    raise exc


def _hold_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RetentionHoldError):
        message = str(exc)
        return HTTPException(404 if "NOT_FOUND" in message else 422, detail=message)
    raise exc


@router.get("/retention-policies")
def list_policies(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.execute(text(
        "SELECT v.id, v.policy_id, v.version_no, v.status, v.rules "
        "FROM retention_policy_versions v ORDER BY v.created_at DESC LIMIT 100"
    )).mappings().all()
    return {"data": {"items": [dict(r) for r in rows], "next_cursor": None, "has_more": False}}


@router.post("/retention-policies", status_code=201)
def create_policy(body: CreateRetentionPolicyRequest, db: Session = Depends(get_db),
                  current_user: User = Depends(require_admin)):
    try:
        result = create_policy_version(db, actor_id=current_user.id,
                                       security_domain_id=body.security_domain_id, rules=body.rules)
    except RetentionPolicyError as exc:
        raise _policy_error(exc)
    return {"data": result}


@router.post("/retention-policies/{version_id}/activate")
def activate_policy(version_id: str, body: ActivateRetentionPolicyRequest, db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin)):
    try:
        result = activate_policy_version(db, actor_id=current_user.id,
                                         security_domain_id=body.security_domain_id,
                                         version_id=version_id, base_epoch=body.base_epoch)
    except RetentionPolicyError as exc:
        raise _policy_error(exc)
    return {"data": result}


@router.get("/retention-holds")
def list_holds(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.execute(text(
        "SELECT id, scope_type, scope_id, reason, released_at IS NOT NULL AS released "
        "FROM retention_holds ORDER BY issued_at DESC LIMIT 100"
    )).mappings().all()
    return {"data": {"items": [dict(r) for r in rows], "next_cursor": None, "has_more": False}}


@router.post("/retention-holds", status_code=201)
def create_hold_route(body: CreateRetentionHoldRequest, db: Session = Depends(get_db),
                      current_user: User = Depends(require_admin)):
    try:
        result = create_hold(db, actor_id=current_user.id, security_domain_id=body.security_domain_id,
                             scope_type=body.scope_type, scope_id=body.scope_id, reason=body.reason)
    except RetentionHoldError as exc:
        raise _hold_error(exc)
    return {"data": result}


@router.post("/retention-holds/{hold_id}/release")
def release_hold_route(hold_id: str, body: ReleaseRetentionHoldRequest, db: Session = Depends(get_db),
                       current_user: User = Depends(require_admin)):
    try:
        result = release_hold(db, actor_id=current_user.id, security_domain_id=body.security_domain_id,
                              hold_id=hold_id)
    except RetentionHoldError as exc:
        raise _hold_error(exc)
    return {"data": result}
