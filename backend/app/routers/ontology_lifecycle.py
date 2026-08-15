"""Ontology lifecycle and release routes (registered by I-BACKEND under
`/api/v1/ontologies`).  Typed envelopes, grant/role guards, stable error
mapping, and idempotent publish replay.  Arbitrary status/version PUT is not
exposed — the state machine owns every transition.
"""
import re

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin, require_editor
from app.models.user import User
from app.schemas.ontology_lifecycle import (
    ArchiveOntologyRequest,
    MarkCreatedRequest,
    OntologyLifecycleResponse,
    OntologyReleaseResponse,
    OntologyReleaseSummaryResponse,
    PublishOntologyRequest,
    RuntimeSwitchRequest,
)
from app.services.agent.catalog import ontology_tool_catalog
from app.services.ontology_access import GrantDenied, require_project_grant
from app.services.publication.lifecycle import (
    LifecycleError,
    archive,
    mark_created,
    publish,
    runtime_disable,
    runtime_enable,
)

router = APIRouter()

_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{16,128}$")


def _require_idempotency_key(key: str | None) -> str:
    if not key or not _IDEMPOTENCY_KEY_PATTERN.match(key):
        raise HTTPException(status_code=400, detail="IDEMPOTENCY_KEY_INVALID")
    return key


def _grant(db: Session, current_user: User, ontology_id: str, capability: str) -> None:
    try:
        require_project_grant(db, current_user, ontology_id, capability)
    except GrantDenied as exc:
        raise HTTPException(status_code=404, detail="ONTOLOGY_NOT_FOUND") from exc


def _lifecycle_error(exc: LifecycleError) -> HTTPException:
    message = str(exc)
    if message in ("INVALID_LIFECYCLE_TRANSITION", "ONTOLOGY_WORKING_REVISION_CONFLICT"):
        return HTTPException(status_code=409, detail=message)
    if message == "NO_SCHEMA_CHANGE":
        return HTTPException(status_code=409, detail="NO_SCHEMA_CHANGE")
    if message.startswith("PUBLICATION_BLOCKED"):
        return HTTPException(status_code=422, detail=message)
    return HTTPException(status_code=404, detail=message)


@router.post("/{ontology_id}/mark-created")
def mark_created_route(
    ontology_id: str,
    body: MarkCreatedRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_idempotency_key(x_idempotency_key)
    _grant(db, current_user, ontology_id, "edit")
    try:
        receipt = mark_created(db, ontology_id=ontology_id, actor_id=current_user.id)
    except LifecycleError as exc:
        raise _lifecycle_error(exc)
    return {"data": OntologyLifecycleResponse(**receipt).model_dump(), "message": "ok"}


@router.post("/{ontology_id}/publish", status_code=201)
def publish_route(
    ontology_id: str,
    body: PublishOntologyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_idempotency_key(x_idempotency_key)
    _grant(db, current_user, ontology_id, "publish")
    try:
        receipt = publish(
            db, ontology_id=ontology_id, actor_id=current_user.id,
            changelog=body.changelog, base_working_revision=body.base_working_revision,
        )
    except LifecycleError as exc:
        raise _lifecycle_error(exc)
    return {"data": receipt, "message": "ok"}


@router.post("/{ontology_id}/archive")
def archive_route(
    ontology_id: str,
    body: ArchiveOntologyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_idempotency_key(x_idempotency_key)
    _grant(db, current_user, ontology_id, "edit")
    try:
        receipt = archive(db, ontology_id=ontology_id, actor_id=current_user.id)
    except LifecycleError as exc:
        raise _lifecycle_error(exc)
    return {"data": OntologyLifecycleResponse(**receipt).model_dump(), "message": "ok"}


@router.post("/{ontology_id}/runtime-disable")
def runtime_disable_route(
    ontology_id: str,
    body: RuntimeSwitchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_idempotency_key(x_idempotency_key)
    _grant(db, current_user, ontology_id, "edit")
    try:
        receipt = runtime_disable(db, ontology_id=ontology_id, actor_id=current_user.id)
    except LifecycleError as exc:
        raise _lifecycle_error(exc)
    return {"data": OntologyLifecycleResponse(**receipt).model_dump(), "message": "ok"}


@router.post("/{ontology_id}/runtime-enable")
def runtime_enable_route(
    ontology_id: str,
    body: RuntimeSwitchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_idempotency_key(x_idempotency_key)
    _grant(db, current_user, ontology_id, "edit")
    try:
        receipt = runtime_enable(db, ontology_id=ontology_id, actor_id=current_user.id)
    except LifecycleError as exc:
        raise _lifecycle_error(exc)
    return {"data": OntologyLifecycleResponse(**receipt).model_dump(), "message": "ok"}


@router.get("/{ontology_id}/releases")
def list_releases(
    ontology_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _grant(db, current_user, ontology_id, "read")
    rows = db.execute(
        sa.text(
            "SELECT id, version_no, version, created_by, created_at FROM ontology_releases "
            "WHERE ontology_id = :o ORDER BY version_no DESC"
        ),
        {"o": ontology_id},
    ).mappings().all()
    items = [OntologyReleaseSummaryResponse(**dict(row)).model_dump() for row in rows]
    return {"data": {"items": items, "next_cursor": None, "has_more": False}, "message": "ok"}


@router.get("/{ontology_id}/releases/{release_id}")
def get_release(
    ontology_id: str,
    release_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _grant(db, current_user, ontology_id, "read")
    row = db.execute(
        sa.text(
            "SELECT id, ontology_id, version_no, version, schema_hash, created_by, created_at, "
            "manifest_projection FROM ontology_releases WHERE ontology_id = :o AND id = :rid"
        ),
        {"o": ontology_id, "rid": release_id},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="RELEASE_NOT_FOUND")
    return {"data": OntologyReleaseResponse(
        release_id=row["id"], ontology_id=row["ontology_id"], version_no=row["version_no"],
        version=row["version"], schema_hash=bytes(row["schema_hash"]).hex(),
        created_by=row["created_by"], created_at=row["created_at"],
        manifest_projection=row["manifest_projection"],
    ).model_dump(), "message": "ok"}


@router.get("/{ontology_id}/tools")
def list_ontology_tools(
    ontology_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P2B-TOOLS exposure: the Ontology's exposed tools (built-in query +
    executable Logic rules + instance Actions) with stable descriptor ids from
    the latest published release manifest (working copy when unpublished)."""
    _grant(db, current_user, ontology_id, "read")
    return {"data": ontology_tool_catalog(db, ontology_id), "message": "ok"}
