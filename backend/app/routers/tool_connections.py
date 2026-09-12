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
    IssueMcpTokenRequest,
    RenameConnectionRequest,
    UpdateConnectionVersionRequest,
)
from app.services.tool_connections import (
    ToolConnectionError,
    activate_connection_version,
    approve_connection_version,
    create_connection,
    create_connection_version,
    create_provider,
    delete_connection_version,
    issue_mcp_token,
    list_connection_versions,
    list_connections,
    list_providers,
    pin_mcp_schema,
    rename_connection,
    test_connection_version,
    update_connection_version,
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


@router.get("/tool-connections/{connection_id}/versions")
def list_connection_versions_route(connection_id: str, db: Session = Depends(get_db),
                                   _: User = Depends(require_admin)):
    return {"data": {"items": list_connection_versions(db, connection_id=connection_id)}}


@router.post("/tool-connections", status_code=201)
def create_connection_route(body: CreateConnectionRequest, db: Session = Depends(get_db),
                            current_user: User = Depends(require_admin)):
    try:
        result = create_connection(db, actor_id=current_user.id, provider_id=body.provider_id)
    except ToolConnectionError as exc:
        raise _error(exc)
    return {"data": result}


@router.put("/tool-connections/{connection_id}")
def rename_connection_route(connection_id: str, body: RenameConnectionRequest, db: Session = Depends(get_db),
                            current_user: User = Depends(require_admin)):
    try:
        result = rename_connection(db, actor_id=current_user.id, connection_id=connection_id, name=body.name)
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
            allowlists=body.allowlists, search_provider=body.search_provider,
        )
    except ToolConnectionError as exc:
        raise _error(exc)
    return {"data": result}


@router.put("/tool-connections/versions/{version_id}")
def update_connection_version_route(version_id: str, body: UpdateConnectionVersionRequest,
                                    db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    try:
        result = update_connection_version(
            db, actor_id=current_user.id, version_id=version_id, endpoint=body.endpoint,
            audience=body.audience, scopes=body.scopes, credential_reference=body.credential_reference,
            allowlists=body.allowlists, search_provider=body.search_provider,
        )
    except ToolConnectionError as exc:
        raise _error(exc)
    return {"data": result}


@router.delete("/tool-connections/versions/{version_id}")
def delete_connection_version_route(version_id: str, db: Session = Depends(get_db),
                                    current_user: User = Depends(require_admin)):
    try:
        result = delete_connection_version(db, actor_id=current_user.id, version_id=version_id)
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


@router.post("/tool-connections/versions/{version_id}/test")
def test_connection_version_route(version_id: str, db: Session = Depends(get_db),
                                  _: User = Depends(require_admin)):
    try:
        result = test_connection_version(db, version_id=version_id)
    except ToolConnectionError as exc:
        raise _error(exc)
    return {"data": result}


@router.post("/tool-connections/versions/{version_id}/mcp/pin-schema")
def pin_mcp_schema_route(version_id: str, db: Session = Depends(get_db),
                         current_user: User = Depends(require_admin)):
    try:
        result = pin_mcp_schema(db, actor_id=current_user.id, version_id=version_id)
    except ToolConnectionError as exc:
        raise _error(exc)
    return {"data": result}


@router.post("/tool-connections/versions/{version_id}/mcp/token", status_code=201)
def issue_mcp_token_route(version_id: str, body: IssueMcpTokenRequest, db: Session = Depends(get_db),
                          current_user: User = Depends(require_admin)):
    try:
        result = issue_mcp_token(
            db, actor_id=current_user.id, version_id=version_id, access_token=body.access_token,
            refresh_token=body.refresh_token, expires_in_seconds=body.expires_in_seconds,
            scope=body.scope, audience=body.audience)
    except ToolConnectionError as exc:
        raise _error(exc)
    return {"data": result}
