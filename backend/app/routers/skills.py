"""Signed Skill admin API (P7C external tools). Admin-only."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.user import User
from app.schemas.skills import CreateSkillPackageRequest, CreateSkillVersionRequest, RenameSkillPackageRequest
from app.services.skills.admin import (
    SkillError, approve_skill_version, create_package, create_skill_version,
    create_skill_version_from_upload, list_skill_packages, list_skill_versions, rename_package,
)

router = APIRouter()


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, SkillError):
        message = str(exc)
        return HTTPException(404 if "NOT_FOUND" in message else 422, detail=message)
    raise exc


@router.get("/skills/packages")
def list_packages(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return {"data": {"items": list_skill_packages(db)}}


@router.post("/skills/packages", status_code=201)
def create_package_route(body: CreateSkillPackageRequest, db: Session = Depends(get_db),
                         current_user: User = Depends(require_admin)):
    return {"data": create_package(db, actor_id=current_user.id, name=body.name)}


@router.put("/skills/packages/{package_id}")
def rename_package_route(package_id: str, body: RenameSkillPackageRequest, db: Session = Depends(get_db),
                         current_user: User = Depends(require_admin)):
    try:
        result = rename_package(db, actor_id=current_user.id, package_id=package_id, name=body.name)
    except SkillError as exc:
        raise _error(exc)
    return {"data": result}


@router.get("/skills/versions")
def list_versions(package_id: str | None = None, db: Session = Depends(get_db),
                  _: User = Depends(require_admin)):
    return {"data": {"items": list_skill_versions(db, package_id=package_id)}}


@router.post("/skills/versions", status_code=201)
def create_version_route(body: CreateSkillVersionRequest, db: Session = Depends(get_db),
                         current_user: User = Depends(require_admin)):
    try:
        result = create_skill_version(
            db, actor_id=current_user.id, package_id=body.package_id,
            manifest=body.manifest, signatures=[s.model_dump() for s in body.signatures])
    except SkillError as exc:
        raise _error(exc)
    return {"data": result}


@router.post("/skills/versions/upload", status_code=201)
async def upload_version_route(package_id: str = Form(...), file: UploadFile = File(...),
                               db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    content = await file.read()
    try:
        result = create_skill_version_from_upload(
            db, actor_id=current_user.id, package_id=package_id,
            filename=file.filename or "", content=content)
    except SkillError as exc:
        raise _error(exc)
    return {"data": result}


@router.post("/skills/versions/{version_id}/approve")
def approve_version_route(version_id: str, db: Session = Depends(get_db),
                          current_user: User = Depends(require_admin)):
    try:
        result = approve_skill_version(db, actor_id=current_user.id, version_id=version_id)
    except SkillError as exc:
        raise _error(exc)
    return {"data": result}
