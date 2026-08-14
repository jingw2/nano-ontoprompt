"""Governed Agent configuration API (P2B-API).

Typed envelopes/cursors, method auth (view_config for reads, edit for writes,
existence-hiding 404), and Idempotency-Key format validation on writes.  The
catalog endpoints return only what the principal's grants and role ceiling
permit; the model catalog is redacted and excludes blocked/archived
identities.
"""
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_editor
from app.models.user import User
from app.schemas.agents import (
    AgentBasicVersionRequest,
    AgentCreateRequest,
    AgentOut,
    AgentVersionOut,
)
from app.services.agent.configuration import (
    AgentConfigConflict,
    AgentConfigError,
    create_agent,
    save_basic_version,
)
from app.services.agent.catalog import agent_catalog_models, agent_catalog_ontologies

router = APIRouter()

_IDEMPOTENCY_PATTERN = re.compile(r"^[\x21-\x7e]{16,128}$")


def _require_idempotency_key(key: str | None) -> None:
    if not key or not _IDEMPOTENCY_PATTERN.match(key):
        raise HTTPException(422, detail="Idempotency-Key must be 16-128 printable ASCII characters")


def _default_application_schema(db: Session) -> str:
    version_id = db.execute(text(
        "SELECT v.id FROM application_state_schema_versions v "
        "JOIN application_state_schema_registries r ON r.active_version_id = v.id "
        "WHERE r.application_key = 'chat-v1'"
    )).scalar_one_or_none()
    if not version_id:
        raise HTTPException(409, detail="CHAT_V1_SCHEMA_UNAVAILABLE")
    return version_id


def _require_agent_grant(db: Session, user_id: str, agent_id: str, capability: str) -> None:
    """Existence-hiding grant check: the caller must hold an active grant with
    the capability; otherwise 404 (the agent may as well not exist)."""
    grant = db.execute(text(
        "SELECT 1 FROM agent_access_grants WHERE agent_id = :id AND user_id = :uid "
        "AND status = 'active' AND capabilities::text LIKE :cap LIMIT 1"
    ), {"id": agent_id, "uid": user_id, "cap": f'%"{capability}"%'}).scalar_one_or_none()
    if not grant:
        raise HTTPException(404, detail="Not found")


def _agent_out(db: Session, agent_id: str) -> dict:
    row = db.execute(text(
        "SELECT a.id, a.status, a.visibility, v.version_no, v.name, v.config_hash, "
        "(SELECT count(*) FROM agent_versions av WHERE av.agent_id = a.id) AS versions_count "
        "FROM agents a LEFT JOIN agent_versions v ON v.id = a.active_version_id "
        "WHERE a.id = :id"
    ), {"id": agent_id}).mappings().one()
    return AgentOut(agent_id=row["id"], status=row["status"], visibility=row["visibility"],
                    name=row["name"], version_no=row["version_no"],
                    config_hash=row["config_hash"], versions_count=row["versions_count"]).model_dump()


@router.get("")
def list_agents(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.execute(text(
        "SELECT a.id FROM agents a "
        "JOIN agent_access_grants g ON g.agent_id = a.id AND g.user_id = :uid "
        "AND g.status = 'active' AND g.capabilities::text LIKE '%\"view_config\"%' "
        "ORDER BY a.created_at DESC"
    ), {"uid": current_user.id}).scalars().all()
    return {"data": {"items": [_agent_out(db, aid) for aid in rows],
                     "next_cursor": None, "has_more": False}}


@router.post("", status_code=201)
def create_agent_route(body: AgentCreateRequest, db: Session = Depends(get_db),
                       current_user: User = Depends(require_editor),
                       x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    _require_idempotency_key(x_idempotency_key)
    app_schema = body.application_state_schema_version_id or _default_application_schema(db)
    try:
        result = create_agent(
            db, actor_id=current_user.id, name=body.name, description=body.description,
            default_model_config_version_id=body.default_model_config_version_id,
            default_model_name=body.default_model_name, system_prompt=body.system_prompt,
            memory_settings=body.memory_settings, application_state_schema_version_id=app_schema,
        )
    except AgentConfigError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"data": result}


@router.get("/catalog/ontologies")
def catalog_ontologies(db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    return {"data": {"items": agent_catalog_ontologies(db, current_user.id, {"discover"}), "next_cursor": None, "has_more": False}}


@router.get("/catalog/models")
def catalog_models(db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    return {"data": {"items": agent_catalog_models(db, {"discover"}), "next_cursor": None, "has_more": False}}


@router.get("/{agent_id}")
def get_agent(agent_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_agent_grant(db, current_user.id, agent_id, "view_config")
    return {"data": _agent_out(db, agent_id)}


@router.post("/{agent_id}/versions", status_code=201)
def create_agent_version(agent_id: str, body: AgentBasicVersionRequest, db: Session = Depends(get_db),
                         current_user: User = Depends(require_editor),
                         x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    _require_idempotency_key(x_idempotency_key)
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    app_schema = body.application_state_schema_version_id or _default_application_schema(db)
    try:
        result = save_basic_version(
            db, actor_id=current_user.id, agent_id=agent_id, base_version_no=body.base_version_no,
            name=body.name, description=body.description,
            default_model_config_version_id=body.default_model_config_version_id,
            default_model_name=body.default_model_name, system_prompt=body.system_prompt,
            memory_settings=body.memory_settings,
            application_state_schema_version_id=app_schema, change_note=body.change_note,
        )
    except AgentConfigConflict as exc:
        raise HTTPException(409, detail=str(exc))
    except AgentConfigError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"data": result}


@router.get("/{agent_id}/versions")
def list_agent_versions(agent_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_agent_grant(db, current_user.id, agent_id, "view_config")
    rows = db.execute(text(
        "SELECT id, version_no, name, description, config_hash, created_at "
        "FROM agent_versions WHERE agent_id = :id ORDER BY version_no"
    ), {"id": agent_id}).mappings().all()
    return {"data": {"items": [AgentVersionOut(**dict(r)).model_dump() for r in rows],
                     "next_cursor": None, "has_more": False}}
