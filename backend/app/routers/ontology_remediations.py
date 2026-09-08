"""Ontology migration-remediation routes (registered by I-BACKEND under
`/api/v1/ontologies`).  Reads require `OntologyProjectAccessGrant.read`;
remediation PATCHes require `edit`; denials hide existence.
"""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.ontology_remediation import (
    RemediateExecutableRequest,
    RemediatePropertyRequest,
)
from app.services.ontology_access import GrantDenied, require_project_grant
from app.services.publication.remediation import (
    RemediationConflict,
    RemediationNotFound,
    remediate_executable,
    remediate_property,
)

router = APIRouter()

_FINDING_COLUMNS = (
    "id, ontology_id, entity_id, kind, item_id, code, path, message, source_hash, "
    "classification, status, revision, created_at"
)


def _require(db: Session, current_user: User, ontology_id: str, capability: str) -> None:
    try:
        require_project_grant(db, current_user, ontology_id, capability)
    except GrantDenied as exc:
        raise HTTPException(status_code=404, detail="ONTOLOGY_NOT_FOUND") from exc


def _finding_rows(db: Session, ontology_id: str, kind: str | None = None,
                  item_id: str | None = None) -> list[dict]:
    statement = f"SELECT {_FINDING_COLUMNS} FROM ontology_migration_findings WHERE ontology_id = :o"
    params: dict = {"o": ontology_id}
    if kind:
        statement += " AND kind = :k"
        params["k"] = kind
    if item_id:
        statement += " AND item_id = :i"
        params["i"] = item_id
    statement += " ORDER BY created_at, id"
    rows = db.execute(sa.text(statement), params).mappings().all()
    return [dict(row) for row in rows]


@router.get("/{ontology_id}/migration-remediations")
def list_remediations(
    ontology_id: str,
    kind: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require(db, current_user, ontology_id, "read")
    return {"data": _finding_rows(db, ontology_id, kind=kind), "message": "ok"}


@router.get("/{ontology_id}/migration-remediations/{kind}/{item_id}")
def remediation_detail(
    ontology_id: str,
    kind: str,
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require(db, current_user, ontology_id, "read")
    rows = _finding_rows(db, ontology_id, kind=kind, item_id=item_id)
    if not rows:
        raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")
    return {"data": rows[0], "message": "ok"}


@router.patch("/{ontology_id}/migration-remediations/{kind}/{item_id}")
def remediate(
    ontology_id: str,
    kind: str,
    item_id: str,
    body: RemediatePropertyRequest | RemediateExecutableRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require(db, current_user, ontology_id, "edit")
    try:
        if kind == "property":
            result = remediate_property(db, ontology_id=ontology_id, request=body, actor_id=current_user.id)
        elif kind == "executable":
            result = remediate_executable(db, ontology_id=ontology_id, request=body, actor_id=current_user.id)
        else:
            raise HTTPException(status_code=404, detail="FINDING_KIND_UNKNOWN")
    except RemediationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RemediationConflict as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"data": result, "message": "ok"}
