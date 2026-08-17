"""Tool provider/connection/version admin API (P7A external tools)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.user import User
from app.schemas.tool_connections import (
    ActivateConnectionVersionRequest,
    CreateConnectionRequest,
    CreateConnectionVersionRequest,
    CreateProviderRequest,
)
from app.services.tool_connections import (
    ToolConnectionError,
    activate_connection_version,
    approve_connection_version,
    create_connection,
    create_connection_version,
    create_provider,
    list_connections,
    list_providers,
)

router = APIRouter()


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ToolConnectionError):
        message = str(exc)
        return HTTPException(404 if "NOT_FOUND" in message else 422, detail=message)
    raise exc


@router.get("/tool-providers")
def list_providers_route(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return {"data": {"items": list_providers(db)}}


@router.post("/tool-providers", status_code=201)
def create_provider_route(body: CreateProviderRequest, db: Session = Depends(get_db),
                          current_user: User = Depends(require_admin)):
    try:
        result = create_provider(db, actor_id=current_user.id, name=body.name, kind=body.kind)
    except ToolConnectionError as exc:
        raise _error(exc)
    return {"data": result}


@router.get("/tool-connections")
def list_connections_route(provider_id: str | None = None, db: Session = Depends(get_db),
                           _: User = Depends(require_admin)):
    return {"data": {"items": list_connections(db, provider_id=provider_id)}}


@router.post("/tool-connections", status_code=201)
def create_connection_route(body: CreateConnectionRequest, db: Session = Depends(get_db),
                            current_user: User = Depends(require_admin)):
    try:
        result = create_connection(db, actor_id=current_user.id, provider_id=body.provider_id)
    except ToolConnectionError as exc:
        raise _error(exc)
    return {"data": result}


@router.post("/tool-connections/versions", status_code=201)
def create_connection_version_route(body: CreateConnectionVersionRequest, db: Session = Depends(get_db),
                                    current_user: User = Depends(require_admin)):
    try:
        result = create_connection_version(
            db, actor_id=current_user.id, connection_id=body.connection_id, endpoint=body.endpoint,
            audience=body.audience, scopes=body.scopes, credential_reference=body.credential_reference,
            allowlists=body.allowlists,
        )
    except ToolConnectionError as exc:
        raise _error(exc)
    return {"data": result}


@router.post("/tool-connections/versions/{version_id}/approve")
def approve_connection_version_route(version_id: str, db: Session = Depends(get_db),
                                     current_user: User = Depends(require_admin)):
    try:
        result = approve_connection_version(db, actor_id=current_user.id, version_id=version_id)
    except ToolConnectionError as exc:
        raise _error(exc)
    return {"data": result}


@router.post("/tool-connections/activate")
def activate_connection_version_route(body: ActivateConnectionVersionRequest, db: Session = Depends(get_db),
                                      current_user: User = Depends(require_admin)):
    try:
        result = activate_connection_version(
            db, actor_id=current_user.id, connection_id=body.connection_id, version_id=body.version_id)
    except ToolConnectionError as exc:
        raise _error(exc)
    return {"data": result}
