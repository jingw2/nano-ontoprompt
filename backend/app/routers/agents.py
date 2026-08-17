"""Governed Agent configuration API (P2B-API).

Typed envelopes/cursors, method auth (view_config for reads, edit for writes,
existence-hiding 404), and Idempotency-Key format validation on writes.  The
catalog endpoints return only what the principal's grants and role ceiling
permit; the model catalog is redacted and excludes blocked/archived
identities.
"""
import base64
import hashlib
import hmac
import re
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_db, get_current_user, require_editor
from app.models.user import User
from app.schemas.agents import (
    AgentAccessGrantOut,
    AgentBasicVersionRequest,
    AgentCreateRequest,
    AgentOut,
    AgentVersionOut,
    CreateAgentAccessGrantRequest,
    PromptGenerationDecisionRequest,
    PromptGenerationOut,
    PromptGenerationRequest,
    RestoreVersionRequest,
    ReviseAgentAccessGrantRequest,
    RevokeAgentAccessGrantRequest,
    ToolValidationRequest,
    ToolValidationResponse,
)
from app.services.agent.access_grants import (
    AgentAccessGrantConflict,
    AgentAccessGrantError,
    create_agent_access_grant,
    list_agent_access_grants,
    revise_agent_access_grant,
    revoke_agent_access_grant,
)
from app.services.agent.catalog import (
    agent_catalog_models,
    agent_catalog_ontologies,
    validate_agent_tools,
    validate_binding_tools,
)
from app.services.agent.tool_categories import validate_categories
from app.services.agent.configuration import (
    AgentConfigConflict,
    AgentConfigError,
    create_agent,
    get_version,
    restore_version,
    save_basic_version,
)
from app.services.agent.policy import ceiling_intersection
from app.services.agent.prompt_generation import (
    PromptGenerationError,
    accept_prompt_generation,
    record_prompt_generation,
    reject_prompt_generation,
)

router = APIRouter()

_IDEMPOTENCY_PATTERN = re.compile(r"^[\x21-\x7e]{16,128}$")

_MAX_LIMIT = 100
_DEFAULT_LIMIT = 50


def _sign_cursor(raw: str) -> str:
    return hmac.new(settings.secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()


def _encode_cursor(created_at: datetime, agent_id: str) -> str:
    raw = f"{created_at.isoformat()}|{agent_id}"
    return base64.urlsafe_b64encode(f"{raw}.{_sign_cursor(raw)}".encode()).decode()


def _decode_cursor(cursor: str):
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        raw, sig = decoded.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign_cursor(raw)):
            return None
        created_at_s, agent_id = raw.split("|", 1)
        return datetime.fromisoformat(created_at_s), agent_id
    except Exception:
        return None


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


def _ontology_data_capabilities(db: Session, user_id: str, ontology_id: str) -> frozenset[str]:
    """The principal's effective data capabilities for one Ontology: the union
    of their active `ontology_data_grants` capabilities on that Ontology,
    capped by their role ceiling (fail closed — no grant means no data
    capability)."""
    rows = db.execute(text(
        "SELECT capabilities FROM ontology_data_grants "
        "WHERE ontology_id = :o AND user_id = :u AND status = 'active'"
    ), {"o": ontology_id, "u": user_id}).scalars().all()
    granted = frozenset().union(*(set(r) for r in rows)) if rows else frozenset()
    return granted


def _validate_ontology_bindings(db: Session, current_user: User, bindings: list[dict]) -> None:
    """§12/P2B-TOOLS config validation for a requested binding set: each bound
    Ontology must be discoverable through the principal's project grant, the
    binding's data capabilities must fit the principal's effective data
    capabilities (data grants ∩ role ceiling), and every selected tool must be
    exposed by the Ontology.  Raises 422 on the first violation."""
    if len(bindings) > 1:
        raise HTTPException(422, detail="AGENTS_BINDING_SINGLE_ONTOLOGY_ONLY")
    for binding in bindings:
        ontology_id = binding["ontology_id"]
        discoverable = db.execute(text(
            "SELECT 1 FROM ontology_project_access_grants "
            "WHERE ontology_id = :o AND user_id = :u AND status = 'active' "
            "AND capabilities::text LIKE '%\"discover\"%' LIMIT 1"
        ), {"o": ontology_id, "u": current_user.id}).scalar_one_or_none()
        if not discoverable:
            raise HTTPException(422, detail="AGENTS_BINDING_ONTOLOGY_UNAVAILABLE")
        requested = frozenset(binding.get("capabilities") or [])
        if not requested:
            raise HTTPException(422, detail="AGENTS_BINDING_CAPABILITIES_REQUIRED")
        if not validate_categories(binding.get("enabled_categories")):
            raise HTTPException(422, detail="AGENTS_BINDING_CATEGORIES_INVALID")
        effective = ceiling_intersection(
            _ontology_data_capabilities(db, current_user.id, ontology_id), current_user.role)
        if not validate_agent_tools(effective, requested):
            raise HTTPException(422, detail="AGENTS_TOOLS_VALIDATION_FAILED")
        if not validate_binding_tools(db, ontology_id, binding.get("selected_tools") or []):
            raise HTTPException(422, detail="AGENTS_TOOLS_SELECTION_INVALID")


def _version_out(row: dict) -> dict:
    """Wire the AgentVersion DTO: legacy bindings without explicit
    `enabled_categories` keep the pre-category response shape (the field is
    omitted when None so old clients and exact-shape consumers are stable)."""
    for binding in row.get("ontology_bindings") or []:
        if binding.get("enabled_categories") is None:
            binding.pop("enabled_categories", None)
    return row


def _version_dump(row: dict) -> dict:
    """model_dump() re-adds default-None fields, so strip them after the
    pydantic dump for legacy bindings (wire-shape backward compatibility)."""
    out = AgentVersionOut(**row).model_dump()
    for binding in out.get("ontology_bindings") or []:
        if binding.get("enabled_categories") is None:
            binding.pop("enabled_categories", None)
    return out


def _agent_out(db: Session, agent_id: str, user_id: str | None = None) -> dict:
    row = db.execute(text(
        "SELECT a.id, a.status, a.visibility, a.created_at, v.version_no, v.name, v.config_hash, "
        "(SELECT count(*) FROM agent_versions av WHERE av.agent_id = a.id) AS versions_count, "
        "(SELECT count(*) FROM agent_access_grants g "
        " WHERE g.agent_id = a.id AND g.user_id = :uid AND g.status = 'active' "
        " AND g.capabilities::text LIKE '%\"edit\"%') AS can_edit "
        "FROM agents a LEFT JOIN agent_versions v ON v.id = a.active_version_id "
        "WHERE a.id = :id"
    ), {"id": agent_id, "uid": user_id or ""}).mappings().one()
    return AgentOut(agent_id=row["id"], status=row["status"], visibility=row["visibility"],
                    name=row["name"], version_no=row["version_no"],
                    config_hash=row["config_hash"], versions_count=row["versions_count"],
                    created_at=row["created_at"], can_edit=bool(row["can_edit"])).model_dump()


@router.get("")
def list_agents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    cursor: str | None = Query(None),
    q: str | None = Query(None),
    id: str | None = Query(None),
    name: str | None = Query(None),
    created_from: datetime | None = Query(None),
    created_before: datetime | None = Query(None),
):
    """Access-filtered agent list: stable `created_at DESC, id DESC` keyset
    pagination with an opaque signed cursor and q/id/name/date filters."""
    filters = []
    params: dict = {"uid": current_user.id, "fetch": limit + 1}
    if cursor:
        pos = _decode_cursor(cursor)
        if pos is None:
            raise HTTPException(422, detail="CURSOR_INVALID")
        cursor_at, cursor_id = pos
        filters.append("(a.created_at < CAST(:cursor_at AS timestamptz) "
                       "OR (a.created_at = CAST(:cursor_at AS timestamptz) AND a.id < :cursor_id))")
        params["cursor_at"] = cursor_at
        params["cursor_id"] = cursor_id
    if q:
        # general search: name substring OR id prefix
        filters.append("(v.name ILIKE :q_like OR a.id::text LIKE :q_prefix)")
        params["q_like"] = f"%{q}%"
        params["q_prefix"] = f"{q}%"
    if id:
        # exact UUID or explicit prefix
        filters.append("(a.id::text = :id_exact OR a.id::text LIKE :id_prefix)")
        params["id_exact"] = id
        params["id_prefix"] = f"{id}%"
    if name:
        filters.append("v.name ILIKE :name_like")
        params["name_like"] = f"%{name}%"
    if created_from:
        filters.append("a.created_at >= :created_from")
        params["created_from"] = created_from
    if created_before:
        filters.append("a.created_at < :created_before")
        params["created_before"] = created_before
    where = " AND ".join(filters) if filters else "TRUE"
    rows = db.execute(text(
        "SELECT a.id, a.status, a.visibility, a.created_at, v.version_no, v.name, v.config_hash, "
        "(SELECT count(*) FROM agent_versions av WHERE av.agent_id = a.id) AS versions_count, "
        "(SELECT count(*) FROM agent_access_grants g "
        " WHERE g.agent_id = a.id AND g.user_id = :uid AND g.status = 'active' "
        " AND g.capabilities::text LIKE '%\"edit\"%') AS can_edit "
        "FROM agents a "
        "JOIN agent_access_grants g ON g.agent_id = a.id AND g.user_id = :uid "
        "AND g.status = 'active' AND g.capabilities::text LIKE '%\"view_config\"%' "
        "LEFT JOIN agent_versions v ON v.id = a.active_version_id "
        f"WHERE {where} "
        "ORDER BY a.created_at DESC, a.id DESC "
        "LIMIT :fetch"
    ), params).mappings().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(last["created_at"], last["id"])
    items = [AgentOut(agent_id=r["id"], status=r["status"], visibility=r["visibility"],
                      name=r["name"], version_no=r["version_no"], config_hash=r["config_hash"],
                      versions_count=r["versions_count"], created_at=r["created_at"],
                      can_edit=bool(r["can_edit"])).model_dump() for r in page]
    return {"data": {"items": items, "next_cursor": next_cursor, "has_more": has_more}}


@router.post("", status_code=201)
def create_agent_route(body: AgentCreateRequest, db: Session = Depends(get_db),
                       current_user: User = Depends(require_editor),
                       x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    _require_idempotency_key(x_idempotency_key)
    app_schema = body.application_state_schema_version_id or _default_application_schema(db)
    bindings = [b.model_dump() for b in body.ontology_bindings]
    _validate_ontology_bindings(db, current_user, bindings)
    try:
        result = create_agent(
            db, actor_id=current_user.id, name=body.name, description=body.description,
            default_model_config_version_id=body.default_model_config_version_id,
            default_model_name=body.default_model_name, system_prompt=body.system_prompt,
            memory_settings=body.memory_settings, application_state_schema_version_id=app_schema,
            ontology_bindings=bindings,
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


@router.post("/{agent_id}/tool-validation")
def validate_agent_tool_bindings(
    agent_id: str, body: ToolValidationRequest, db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """§12 catalog/config validation: the requested ontology bindings must be
    discoverable by the principal (project grant) and the binding's data
    capabilities must fit the principal's effective data capabilities (data
    grants ∩ role ceiling) — P2B-POLICY path, fail closed.  Read-only POST, so
    no Idempotency-Key is required."""
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    blocked: list[str] = []
    capabilities: list[str] = []
    required = frozenset({"read_schema", "read_instances", "traverse_relations"})
    for ontology_id in body.ontology_ids:
        discoverable = db.execute(text(
            "SELECT 1 FROM ontology_project_access_grants "
            "WHERE ontology_id = :o AND user_id = :u AND status = 'active' "
            "AND capabilities::text LIKE '%\"discover\"%' LIMIT 1"
        ), {"o": ontology_id, "u": current_user.id}).scalar_one_or_none()
        if not discoverable:
            blocked.append(ontology_id)
            continue
        effective = ceiling_intersection(
            _ontology_data_capabilities(db, current_user.id, ontology_id), current_user.role)
        if not validate_agent_tools(effective, required):
            blocked.append(ontology_id)
            continue
        capabilities.extend(sorted(required & effective))
    valid = len(blocked) == 0
    return {"data": ToolValidationResponse(valid=valid, blocked=blocked,
                                           capabilities=sorted(set(capabilities))).model_dump()}


@router.delete("/{agent_id}", status_code=204)
def archive_agent(agent_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    result = db.execute(text(
        "UPDATE agents SET status = 'archived', updated_at = now() "
        "WHERE id = :id AND status = 'active'"
    ), {"id": agent_id})
    if result.rowcount != 1:
        raise HTTPException(404, detail="Not found")
    db.commit()
    return None


@router.get("/{agent_id}")
def get_agent(agent_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_agent_grant(db, current_user.id, agent_id, "view_config")
    return {"data": _agent_out(db, agent_id, current_user.id)}


@router.post("/{agent_id}/versions", status_code=201)
def create_agent_version(agent_id: str, body: AgentBasicVersionRequest, db: Session = Depends(get_db),
                         current_user: User = Depends(require_editor),
                         x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    _require_idempotency_key(x_idempotency_key)
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    app_schema = body.application_state_schema_version_id or _default_application_schema(db)
    bindings = [b.model_dump() for b in body.ontology_bindings] if body.ontology_bindings is not None else None
    if bindings is not None:
        _validate_ontology_bindings(db, current_user, bindings)
    try:
        result = save_basic_version(
            db, actor_id=current_user.id, agent_id=agent_id, base_version_no=body.base_version_no,
            name=body.name, description=body.description,
            default_model_config_version_id=body.default_model_config_version_id,
            default_model_name=body.default_model_name, system_prompt=body.system_prompt,
            memory_settings=body.memory_settings,
            application_state_schema_version_id=app_schema, change_note=body.change_note,
            ontology_bindings=bindings,
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
        "SELECT id, version_no, name, description, config_hash, "
        "default_model_config_version_id, default_model_name, system_prompt, memory_settings, "
        "application_state_schema_version_id, change_note, prompt_generation_id, created_by, created_at "
        "FROM agent_versions WHERE agent_id = :id ORDER BY version_no"
    ), {"id": agent_id}).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        item["ontology_bindings"] = list(get_version(db, agent_id=agent_id,
                                                     version_no=row["version_no"])["ontology_bindings"])
        items.append(item)
    return {"data": {"items": [_version_dump(item) for item in items],
                     "next_cursor": None, "has_more": False}}


@router.get("/{agent_id}/versions/{version_no}")
def get_agent_version_route(agent_id: str, version_no: int, db: Session = Depends(get_db),
                            current_user: User = Depends(get_current_user)):
    _require_agent_grant(db, current_user.id, agent_id, "view_config")
    row = get_version(db, agent_id=agent_id, version_no=version_no)
    if row is None:
        raise HTTPException(404, detail="Not found")
    return {"data": _version_dump(dict(_version_out(row)))}


@router.post("/{agent_id}/versions/{version_no}/restore", status_code=201)
def restore_agent_version(agent_id: str, version_no: int, body: RestoreVersionRequest,
                          db: Session = Depends(get_db),
                          current_user: User = Depends(require_editor),
                          x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    _require_idempotency_key(x_idempotency_key)
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    try:
        result = restore_version(
            db, actor_id=current_user.id, agent_id=agent_id,
            source_version_no=version_no, change_note=body.change_note,
        )
    except AgentConfigConflict as exc:
        raise HTTPException(409, detail=str(exc))
    except AgentConfigError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"data": result}


@router.get("/{agent_id}/access-grants")
def agent_access_grants_list(agent_id: str, db: Session = Depends(get_db),
                             current_user: User = Depends(require_editor)):
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    return {"data": list_agent_access_grants(db, agent_id=agent_id)}


@router.post("/{agent_id}/access-grants", status_code=201)
def agent_access_grants_create(agent_id: str, body: CreateAgentAccessGrantRequest,
                               db: Session = Depends(get_db),
                               current_user: User = Depends(require_editor),
                               x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    _require_idempotency_key(x_idempotency_key)
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    try:
        result = create_agent_access_grant(
            db, actor_id=current_user.id, agent_id=agent_id,
            user_id=body.user_id, capabilities=body.capabilities,
        )
    except AgentAccessGrantError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"data": AgentAccessGrantOut(**result).model_dump()}


@router.post("/{agent_id}/access-grants/{grant_id}/revisions", status_code=201)
def agent_access_grants_revise(agent_id: str, grant_id: str, body: ReviseAgentAccessGrantRequest,
                               db: Session = Depends(get_db),
                               current_user: User = Depends(require_editor),
                               x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    _require_idempotency_key(x_idempotency_key)
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    try:
        result = revise_agent_access_grant(
            db, actor_id=current_user.id, grant_id=grant_id,
            base_revision=body.base_revision, capabilities=body.capabilities,
        )
    except AgentAccessGrantConflict as exc:
        raise HTTPException(409, detail=str(exc))
    except AgentAccessGrantError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"data": AgentAccessGrantOut(**result).model_dump()}


@router.post("/{agent_id}/access-grants/{grant_id}/revoke")
def agent_access_grants_revoke(agent_id: str, grant_id: str, body: RevokeAgentAccessGrantRequest,
                               db: Session = Depends(get_db),
                               current_user: User = Depends(require_editor),
                               x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    _require_idempotency_key(x_idempotency_key)
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    try:
        result = revoke_agent_access_grant(
            db, actor_id=current_user.id, grant_id=grant_id, base_revision=body.base_revision,
        )
    except AgentAccessGrantConflict as exc:
        raise HTTPException(409, detail=str(exc))
    except AgentAccessGrantError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"data": AgentAccessGrantOut(**result).model_dump()}


def _sanitize_prompt_text(value: str) -> str:
    """Sanitized fields only: strip HTML/scripts and control characters, cap
    length (mirrors the frontend CSP-safe rendering contract)."""
    import html as _html
    stripped = _html.unescape(value)
    stripped = re.sub(r"<[^>]*>", "", stripped)
    stripped = "".join(ch for ch in stripped if ch == "\n" or ord(ch) >= 32)
    return stripped[:20000].strip()


def _prompt_generation_out(db: Session, generation_id: str) -> dict:
    row = db.execute(text(
        "SELECT id, agent_id, base_version_no, model_config_version_id, model_name, "
        "input_hash, output_hash, status, requester_id, requested_at, accepted_at "
        "FROM prompt_generations WHERE id = :id"
    ), {"id": generation_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, detail="Not found")
    return PromptGenerationOut(**dict(row)).model_dump()


@router.post("/{agent_id}/prompt-generations", status_code=202)
def create_prompt_generation(
    agent_id: str, body: PromptGenerationRequest, db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Record a sanitized prompt generation (202 accepted).  MVP contract:
    the sanitized draft is the synchronous suggestion baseline — input and
    output hashes both derive from it and `output_text` is returned for
    Replace/Append; generation is never auto-activated."""
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    _require_idempotency_key(x_idempotency_key)
    sanitized = _sanitize_prompt_text(body.input_text)
    digest = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
    try:
        result = record_prompt_generation(
            db, actor_id=current_user.id, agent_id=agent_id, base_version_no=body.base_version_no,
            model_config_version_id=body.model_config_version_id, model_name=body.model_name,
            input_hash=digest, output_hash=digest,
        )
    except PromptGenerationError as exc:
        raise HTTPException(409, detail=str(exc))
    out = _prompt_generation_out(db, result["id"])
    out["output_text"] = sanitized
    return {"data": out}


@router.get("/{agent_id}/prompt-generations/{generation_id}")
def get_prompt_generation(agent_id: str, generation_id: str, db: Session = Depends(get_db),
                          current_user: User = Depends(require_editor)):
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    row = db.execute(text(
        "SELECT agent_id FROM prompt_generations WHERE id = :id"
    ), {"id": generation_id}).mappings().one_or_none()
    if row is None or row["agent_id"] != agent_id:
        raise HTTPException(404, detail="Not found")
    return {"data": _prompt_generation_out(db, generation_id)}


@router.post("/{agent_id}/prompt-generations/{generation_id}/decision")
def decide_prompt_generation(
    agent_id: str, generation_id: str, body: PromptGenerationDecisionRequest,
    db: Session = Depends(get_db), current_user: User = Depends(require_editor),
    x_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_agent_grant(db, current_user.id, agent_id, "edit")
    _require_idempotency_key(x_idempotency_key)
    row = db.execute(text(
        "SELECT agent_id FROM prompt_generations WHERE id = :id"
    ), {"id": generation_id}).mappings().one_or_none()
    if row is None or row["agent_id"] != agent_id:
        raise HTTPException(404, detail="Not found")
    try:
        if body.decision == "accepted":
            accept_prompt_generation(db, actor_id=current_user.id, agent_id=agent_id, generation_id=generation_id)
        else:
            reject_prompt_generation(db, actor_id=current_user.id, agent_id=agent_id, generation_id=generation_id)
    except PromptGenerationError as exc:
        raise HTTPException(409, detail=str(exc))
    return {"data": _prompt_generation_out(db, generation_id)}
