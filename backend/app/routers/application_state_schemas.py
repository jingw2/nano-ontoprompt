"""Application state schema admin API (P3B-STATEADMIN).

Administrator `POST /api/v2/application-state-schemas` atomically creates a
registry plus immutable version 1; nested version POST requires
`base_active_revision`, canonicalizes and validates limits/hash, inserts N+1
inactive; activation CASes the registry pointer; `target_revision=null`
archives (rejected while an active AgentVersion references it).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.models.user import User
from app.schemas.application_state_schema import (
    ActivateApplicationStateSchemaRequest,
    CreateApplicationStateSchemaRequest,
    CreateSchemaVersionRequest,
)
from app.services.application_state_schema import (
    ApplicationStateSchemaConflict,
    ApplicationStateSchemaError,
    activate_schema_version,
    create_registry_with_version,
    create_schema_version,
    list_schema_registries,
)

router = APIRouter()


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ApplicationStateSchemaConflict):
        return HTTPException(409, detail=str(exc))
    if isinstance(exc, ApplicationStateSchemaError):
        message = str(exc)
        if "NOT_FOUND" in message:
            return HTTPException(404, detail=message)
        return HTTPException(422, detail=message)
    raise exc


@router.get("")
def list_registries(db: Session = Depends(get_db), _=Depends(require_admin)):
    return {"data": list_schema_registries(db)}


@router.post("", status_code=201)
def create_registry(body: CreateApplicationStateSchemaRequest, db: Session = Depends(get_db),
                    current_user: User = Depends(require_admin)):
    try:
        result = create_registry_with_version(db, actor_id=current_user.id,
                                              application_key=body.application_key,
                                              json_schema=body.json_schema)
    except ApplicationStateSchemaError as exc:
        raise _error(exc)
    return {"data": result}


@router.post("/{registry_id}/versions", status_code=201)
def add_version(registry_id: str, body: CreateSchemaVersionRequest, db: Session = Depends(get_db),
                current_user: User = Depends(require_admin)):
    try:
        result = create_schema_version(db, actor_id=current_user.id, registry_id=registry_id,
                                       base_active_revision=body.base_active_revision,
                                       json_schema=body.json_schema)
    except ApplicationStateSchemaError as exc:
        raise _error(exc)
    return {"data": result}


@router.post("/{registry_id}/activate")
def activate(registry_id: str, body: ActivateApplicationStateSchemaRequest, db: Session = Depends(get_db),
             current_user: User = Depends(require_admin)):
    try:
        result = activate_schema_version(db, actor_id=current_user.id, registry_id=registry_id,
                                         base_active_revision=body.base_active_revision,
                                         target_revision=body.target_revision)
    except ApplicationStateSchemaError as exc:
        raise _error(exc)
    return {"data": result}
