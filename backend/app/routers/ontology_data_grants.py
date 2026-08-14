"""Ontology data grant API (P2B-DATAGRANT).

Section 12: `GET/POST /api/v1/ontology-data-grants`,
`POST /api/v1/ontology-data-grants/{grant_id}/revisions`,
`POST /api/v1/ontology-data-grants/{grant_id}/revoke`.
Admin or delegated data-governance authority only.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_editor
from app.models.user import User
from app.schemas.ontology_data_grant import (
    CreateDataGrantRequest,
    ReviseDataGrantRequest,
    RevokeDataGrantRequest,
)
from app.services.ontology_data_grant import (
    DataGrantError,
    DataGrantRevisionConflict,
    create_data_grant,
    list_data_grants,
    revise_data_grant,
    revoke_data_grant,
)

router = APIRouter()


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, DataGrantRevisionConflict):
        return HTTPException(409, detail=str(exc))
    if isinstance(exc, DataGrantError):
        message = str(exc)
        if "NOT_FOUND" in message:
            return HTTPException(404, detail=message)
        if "FORBIDDEN" in message or "UNKNOWN_CAPABILITY" in message:
            return HTTPException(403, detail=message)
        return HTTPException(422, detail=message)
    raise exc


@router.get("")
def list_grants(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return {"data": list_data_grants(db, actor_id=current_user.id)}


@router.post("", status_code=201)
def create_grant(body: CreateDataGrantRequest, db: Session = Depends(get_db),
                 current_user: User = Depends(require_editor)):
    try:
        grant = create_data_grant(db, actor_id=current_user.id, user_id=body.user_id,
                                  ontology_id=body.ontology_id, capabilities=body.capabilities,
                                  entity_allowlist=body.entity_allowlist,
                                  property_allowlist=body.property_allowlist,
                                  relation_allowlist=body.relation_allowlist,
                                  action_allowlist=body.action_allowlist,
                                  policy_version=body.policy_version, row_policy=body.row_policy,
                                  valid_from=body.valid_from, valid_until=body.valid_until)
    except DataGrantError as exc:
        raise _error(exc)
    return {"data": grant}


@router.post("/{grant_id}/revisions", status_code=201)
def revise_grant(grant_id: str, body: ReviseDataGrantRequest, db: Session = Depends(get_db),
                 current_user: User = Depends(require_editor)):
    try:
        grant = revise_data_grant(db, actor_id=current_user.id, grant_id=grant_id,
                                  base_revision=body.base_revision,
                                  capabilities=body.capabilities, row_policy=body.row_policy,
                                  valid_until=body.valid_until)
    except DataGrantError as exc:
        raise _error(exc)
    return {"data": grant}


@router.post("/{grant_id}/revoke")
def revoke_grant(grant_id: str, body: RevokeDataGrantRequest, db: Session = Depends(get_db),
                 current_user: User = Depends(require_editor)):
    try:
        grant = revoke_data_grant(db, actor_id=current_user.id, grant_id=grant_id,
                                  base_revision=body.base_revision, reason=body.reason)
    except DataGrantError as exc:
        raise _error(exc)
    return {"data": grant}
