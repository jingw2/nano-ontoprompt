"""OntologyProjectAccessGrant and owner-recovery routes.

`router` is registered by I-BACKEND under `/api/v1/ontologies`;
`admin_router` is registered under `/api/v1`.  Every method is audited and
denies without disclosing existence.
"""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.models.user import User
from app.schemas.ontology_access import (
    CreateOntologyProjectAccessGrantRequest,
    OntologyProjectAccessGrantResponse,
    RecoverOntologyOwnerRequest,
    ReviseOntologyProjectAccessGrantRequest,
    RevokeOntologyProjectAccessGrantRequest,
)
from app.services.ontology_access import (
    GrantConflict,
    GrantDenied,
    create_grant,
    recover_owner,
    require_project_grant,
    revise_grant,
    revoke_grant,
)

router = APIRouter()
admin_router = APIRouter()

_GRANT_COLUMNS = (
    "id, ontology_id, user_id, capabilities, revision, status, created_by, "
    "revoked_by, valid_from, valid_until, created_at, updated_at, revoked_at"
)


def _hide_denial(exc: GrantDenied):
    raise HTTPException(status_code=404, detail="ONTOLOGY_NOT_FOUND") from exc


def _grant_rows(db: Session, ontology_id: str) -> list[dict]:
    rows = db.execute(
        sa.text(f"SELECT {_GRANT_COLUMNS} FROM ontology_project_access_grants "
                "WHERE ontology_id = :o ORDER BY created_at, id"),
        {"o": ontology_id},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.get("/{ontology_id}/access-grants")
def list_access_grants(
    ontology_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        require_project_grant(db, current_user, ontology_id, "edit")
    except GrantDenied as exc:
        _hide_denial(exc)
    return {"data": _grant_rows(db, ontology_id), "message": "ok"}


@router.post("/{ontology_id}/access-grants", status_code=201)
def create_access_grant(
    ontology_id: str,
    body: CreateOntologyProjectAccessGrantRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        grant = create_grant(
            db, ontology_id=ontology_id, user_id=body.user_id,
            capabilities=body.capabilities, base_revision=body.base_revision,
            actor_id=current_user.id,
        )
    except GrantDenied as exc:
        _hide_denial(exc)
    except GrantConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"data": grant, "message": "ok"}


@router.post("/{ontology_id}/access-grants/{grant_id}/revisions")
def revise_access_grant(
    ontology_id: str,
    grant_id: str,
    body: ReviseOntologyProjectAccessGrantRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        grant = revise_grant(
            db, grant_id=grant_id, capabilities=body.capabilities,
            base_revision=body.base_revision, actor_id=current_user.id,
        )
    except GrantDenied as exc:
        _hide_denial(exc)
    except GrantConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"data": grant, "message": "ok"}


@router.post("/{ontology_id}/access-grants/{grant_id}/revoke")
def revoke_access_grant(
    ontology_id: str,
    grant_id: str,
    body: RevokeOntologyProjectAccessGrantRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        grant = revoke_grant(
            db, grant_id=grant_id, base_revision=body.base_revision, actor_id=current_user.id,
        )
    except GrantDenied as exc:
        _hide_denial(exc)
    except GrantConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"data": grant, "message": "ok"}


@admin_router.post("/admin/ontology-owner-recoveries/{ontology_id}/assign", status_code=201)
def assign_owner_recovery(
    ontology_id: str,
    body: RecoverOntologyOwnerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        grant = recover_owner(
            db, ontology_id=ontology_id, base_finding_revision=body.base_finding_revision,
            assignee_user_id=body.assignee_user_id, actor_id=current_user.id,
        )
    except GrantDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except GrantConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"data": grant, "message": "ok"}
