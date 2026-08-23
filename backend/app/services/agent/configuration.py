"""Immutable agent configuration (P2B-CONFIG).

Creating an Agent is one transaction: Agent + AgentVersion v1 + exact
owner-user grant (all five capabilities) + audit outbox; any failure rolls
back every row.  Every Basic save locks the Agent, clones the full immutable
tree (ontology bindings, external tool bindings, retrieval sources), applies
the complete patch, hashes the configuration, inserts N+1 and CAS-activates
under `base_version_no` — old versions stay byte-identical and there is never
a fallback.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.agent.memory_settings import MemorySettingsError, validate_memory_settings

AGENT_CAPABILITIES = ("discover", "run", "view_config", "edit", "view_audit")


class AgentConfigError(Exception):
    """Rejected agent-configuration operation."""


class AgentConfigConflict(AgentConfigError):
    """base_version_no CAS mismatch (concurrent save)."""


def _new_id() -> str:
    return str(uuid.uuid4())


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _normalize_binding(binding: dict) -> dict:
    """Canonical binding shape for hashing: always includes ontology_id,
    capabilities, allowlists and selected_tools; `enabled_categories` is
    included only when present (None keeps the legacy selected_tools-only
    filter).  Old-version hashes stay byte-identical and new saves are
    deterministic regardless of client shape."""
    normalized = {
        "ontology_id": binding["ontology_id"],
        "capabilities": list(binding.get("capabilities") or []),
        "allowlists": binding.get("allowlists") or {},
        "selected_tools": list(binding.get("selected_tools") or []),
    }
    if binding.get("enabled_categories") is not None:
        normalized["enabled_categories"] = list(binding["enabled_categories"])
    return normalized


def _audit(db: Session, *, actor_id: str, agent_id: str, operation: str, payload: dict) -> None:
    domain = db.execute(text(
        "SELECT security_domain_id FROM users WHERE id = :id"
    ), {"id": actor_id}).scalar_one_or_none()
    db.execute(text(
        "INSERT INTO governance_audit_outbox "
        "(id, security_domain_id, correlation_id, payload, state, attempts, created_at, updated_at) "
        "VALUES (:id, :domain, :corr, CAST(:payload AS jsonb), 'pending', 0, now(), now())"
    ), {
        "id": _new_id(),
        "domain": domain,
        "corr": f"ag:{agent_id[-8:]}:{_new_id()[:8]}",
        "payload": json.dumps({"event_type": "agent_configuration", "operation": operation,
                               "agent_id": agent_id, "actor_id": actor_id, **payload}, sort_keys=True),
    })


def _verify_model_version(db: Session, model_config_version_id: str, model_name: str) -> None:
    row = db.execute(text(
        "SELECT v.model_config_id, v.provider FROM model_config_versions v WHERE v.id = :id"
    ), {"id": model_config_version_id}).mappings().one_or_none()
    if not row:
        raise AgentConfigError("MODEL_VERSION_UNAVAILABLE")
    # the version must be the ACTIVE version of its identity (no stale pins)
    active = db.execute(text(
        "SELECT 1 FROM model_configs WHERE active_version_id = :id LIMIT 1"
    ), {"id": model_config_version_id}).scalar_one_or_none()
    if not active:
        raise AgentConfigError("MODEL_VERSION_UNAVAILABLE")


def _child_tree(db: Session, agent_version_id: str) -> dict:
    bindings = db.execute(text(
        "SELECT ontology_id, capabilities, allowlists, selected_tools, enabled_categories "
        "FROM agent_ontology_bindings WHERE agent_version_id = :id ORDER BY ontology_id"
    ), {"id": agent_version_id}).mappings().all()
    tools = db.execute(text(
        "SELECT tool_connection_version_id, alias FROM agent_external_tool_bindings "
        "WHERE agent_version_id = :id ORDER BY alias"
    ), {"id": agent_version_id}).mappings().all()
    sources = db.execute(text(
        "SELECT source_id, revision, kind, config_hash, applicability_hash FROM agent_retrieval_sources "
        "WHERE agent_version_id = :id ORDER BY source_id"
    ), {"id": agent_version_id}).mappings().all()
    return {
        "ontology_bindings": [_normalize_binding(dict(b)) for b in bindings],
        "external_tool_bindings": [dict(t) for t in tools],
        "retrieval_sources": [dict(s) for s in sources],
    }


def config_hash(*, name, description, default_model_config_version_id, default_model_name,
                system_prompt, memory_settings, application_state_schema_version_id,
                prompt_generation_id, child_tree) -> str:
    payload = _canonical({
        "name": name,
        "description": description,
        "default_model_config_version_id": default_model_config_version_id,
        "default_model_name": default_model_name,
        "system_prompt": system_prompt,
        "memory_settings": memory_settings,
        "application_state_schema_version_id": application_state_schema_version_id,
        "prompt_generation_id": prompt_generation_id,
        **child_tree,
    })
    return hashlib.sha256(payload.encode()).hexdigest()


def create_agent(
    db: Session, *, actor_id: str, name: str, description: str | None,
    default_model_config_version_id: str, default_model_name: str,
    system_prompt: str | None, memory_settings: dict, application_state_schema_version_id: str,
    ontology_bindings: list[dict] | None = None,
) -> dict:
    """One transaction: Agent + AgentVersion v1 + owner grant + audit."""
    try:
        memory_settings = validate_memory_settings(memory_settings or {})
    except MemorySettingsError as exc:
        raise AgentConfigError(str(exc)) from exc
    _verify_model_version(db, default_model_config_version_id, default_model_name)
    bindings = [_normalize_binding(dict(b)) for b in (ontology_bindings or [])]
    agent_id = _new_id()
    version_id = _new_id()
    digest = config_hash(
        name=name, description=description,
        default_model_config_version_id=default_model_config_version_id,
        default_model_name=default_model_name, system_prompt=system_prompt,
        memory_settings=memory_settings,
        application_state_schema_version_id=application_state_schema_version_id,
        prompt_generation_id=None,
        child_tree={"ontology_bindings": bindings,
                    "external_tool_bindings": [], "retrieval_sources": []},
    )
    db.execute(text(
        "INSERT INTO agents (id, visibility, status, owner_id, active_version_id, created_at, updated_at) "
        "VALUES (:id, 'private', 'active', :owner, NULL, now(), now())"
    ), {"id": agent_id, "owner": actor_id})
    db.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, description, "
        "default_model_config_version_id, default_model_name, system_prompt, memory_settings, "
        "application_state_schema_version_id, config_hash, created_by, created_at) "
        "VALUES (:id, :agent, 1, :name, :desc, :mvid, :mname, :sp, CAST(:mem AS json), "
        " :asv, :hash, :actor, now())"
    ), {"id": version_id, "agent": agent_id, "name": name, "desc": description,
        "mvid": default_model_config_version_id, "mname": default_model_name,
        "sp": system_prompt, "mem": _canonical(memory_settings),
        "asv": application_state_schema_version_id, "hash": digest, "actor": actor_id})
    db.execute(text(
        "UPDATE agents SET active_version_id = :vid WHERE id = :aid"
    ), {"vid": version_id, "aid": agent_id})
    for binding in bindings:
        _insert_binding(db, version_id, binding)
    db.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, "
        "created_by, created_at, updated_at) "
        "VALUES (:id, :agent, :uid, CAST(:caps AS json), 1, 'active', :actor, now(), now())"
    ), {"id": _new_id(), "agent": agent_id, "uid": actor_id,
        "caps": _canonical(list(AGENT_CAPABILITIES)), "actor": actor_id})
    _audit(db, actor_id=actor_id, agent_id=agent_id, operation="create",
           payload={"version": 1, "config_hash": digest})
    db.commit()
    return {"agent_id": agent_id, "version_id": version_id, "version_no": 1, "config_hash": digest}


def _clone_child_rows(db: Session, source_version_id: str, target_version_id: str) -> None:
    bindings = db.execute(text(
        "SELECT ontology_id, capabilities, allowlists, selected_tools, enabled_categories "
        "FROM agent_ontology_bindings WHERE agent_version_id = :id ORDER BY ontology_id"
    ), {"id": source_version_id}).mappings().all()
    for row in bindings:
        _insert_binding(db, target_version_id, _normalize_binding(dict(row)))
    tools = db.execute(text(
        "SELECT tool_connection_version_id, alias FROM agent_external_tool_bindings "
        "WHERE agent_version_id = :id ORDER BY alias"
    ), {"id": source_version_id}).mappings().all()
    for row in tools:
        db.execute(text(
            "INSERT INTO agent_external_tool_bindings (id, agent_version_id, tool_connection_version_id, alias) "
            "VALUES (:id, :av, :tcv, :alias)"
        ), {"id": _new_id(), "av": target_version_id, "tcv": row["tool_connection_version_id"],
            "alias": row["alias"]})
    sources = db.execute(text(
        "SELECT source_id, revision, kind, config, config_hash, applicability_hash "
        "FROM agent_retrieval_sources WHERE agent_version_id = :id"
    ), {"id": source_version_id}).mappings().all()
    for row in sources:
        db.execute(text(
            "INSERT INTO agent_retrieval_sources (id, agent_version_id, source_id, revision, kind, "
            "config, config_hash, applicability_hash, created_at) "
            "VALUES (:id, :av, :sid, :rev, :kind, CAST(:cfg AS json), :ch, :ah, now())"
        ), {"id": _new_id(), "av": target_version_id, "sid": row["source_id"], "rev": row["revision"],
            "kind": row["kind"], "cfg": _canonical(row["config"] or {}),
            "ch": row["config_hash"], "ah": row["applicability_hash"]})


def _insert_binding(db: Session, agent_version_id: str, binding: dict) -> None:
    """Insert one immutable ontology-binding child row.  JSON columns are
    passed as canonical strings with an explicit json cast (works on both
    PostgreSQL and SQLite text() execution).  `enabled_categories` NULL keeps
    the legacy selected_tools-only filter for pre-category bindings."""
    db.execute(text(
        "INSERT INTO agent_ontology_bindings "
        "(id, agent_version_id, ontology_id, capabilities, allowlists, selected_tools, "
        " enabled_categories, created_at) "
        "VALUES (:id, :av, :o, CAST(:caps AS json), CAST(:al AS json), CAST(:st AS json), "
        " :ec, now())"
    ), {
        "id": _new_id(), "av": agent_version_id, "o": binding["ontology_id"],
        "caps": _canonical(binding.get("capabilities") or []),
        "al": _canonical(binding.get("allowlists") or {}),
        "st": _canonical(binding.get("selected_tools") or []),
        "ec": _canonical(binding["enabled_categories"]) if binding.get("enabled_categories") is not None else None,
    })


def save_basic_version(
    db: Session, *, actor_id: str, agent_id: str, base_version_no: int,
    name: str, description: str | None, default_model_config_version_id: str,
    default_model_name: str, system_prompt: str | None, memory_settings: dict,
    application_state_schema_version_id: str, change_note: str | None = None,
    prompt_generation_id: str | None = None,
    ontology_bindings: list[dict] | None = None,
) -> dict:
    """Lock the Agent, clone the tree, apply the complete Basic patch, hash,
    insert N+1 and CAS-activate.  Old versions stay byte-identical.

    `ontology_bindings` (when given) REPLACES the ontology-binding child rows of
    the new version with the complete requested set (each row carries the bound
    ontology plus the enabled tool selection/categories)."""
    try:
        memory_settings = validate_memory_settings(memory_settings or {})
    except MemorySettingsError as exc:
        raise AgentConfigError(str(exc)) from exc
    _verify_model_version(db, default_model_config_version_id, default_model_name)
    bindings = [_normalize_binding(dict(b)) for b in (ontology_bindings or [])]
    agent = db.execute(text(
        "SELECT id, status, active_version_id FROM agents WHERE id = :id FOR UPDATE"
    ), {"id": agent_id}).mappings().one_or_none()
    if not agent or agent["status"] != "active":
        raise AgentConfigError("AGENT_NOT_FOUND")
    active = db.execute(text(
        "SELECT id, version_no FROM agent_versions WHERE id = :id"
    ), {"id": agent["active_version_id"]}).mappings().one_or_none()
    if not active or active["version_no"] != base_version_no:
        raise AgentConfigConflict("AGENT_VERSION_CONFLICT")
    source_version_id = active["id"]
    version_id = _new_id()
    child_tree = _child_tree(db, source_version_id)
    if ontology_bindings is not None:
        child_tree["ontology_bindings"] = bindings
    digest = config_hash(
        name=name, description=description,
        default_model_config_version_id=default_model_config_version_id,
        default_model_name=default_model_name, system_prompt=system_prompt,
        memory_settings=memory_settings,
        application_state_schema_version_id=application_state_schema_version_id,
        prompt_generation_id=prompt_generation_id, child_tree=child_tree,
    )
    db.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, description, "
        "default_model_config_version_id, default_model_name, system_prompt, memory_settings, "
        "application_state_schema_version_id, config_hash, change_note, prompt_generation_id, "
        "created_by, created_at) "
        "VALUES (:id, :agent, :rev, :name, :desc, :mvid, :mname, :sp, CAST(:mem AS json), "
        " :asv, :hash, :note, :pg, :actor, now())"
    ), {"id": version_id, "agent": agent_id, "rev": active["version_no"] + 1,
        "name": name, "desc": description, "mvid": default_model_config_version_id,
        "mname": default_model_name, "sp": system_prompt, "mem": _canonical(memory_settings),
        "asv": application_state_schema_version_id, "hash": digest, "note": change_note,
        "pg": prompt_generation_id, "actor": actor_id})
    _clone_child_rows(db, source_version_id, version_id)
    if ontology_bindings is not None:
        # the new version's binding rows are cloned above; replace them with the patch
        db.execute(text(
            "DELETE FROM agent_ontology_bindings WHERE agent_version_id = :id"
        ), {"id": version_id})
        for binding in bindings:
            _insert_binding(db, version_id, binding)
    result = db.execute(text(
        "UPDATE agents SET active_version_id = :vid, updated_at = now() "
        "WHERE id = :aid AND active_version_id = :old"
    ), {"vid": version_id, "aid": agent_id, "old": source_version_id})
    if result.rowcount != 1:
        raise AgentConfigConflict("AGENT_VERSION_CONFLICT")
    _audit(db, actor_id=actor_id, agent_id=agent_id, operation="save_basic",
           payload={"base_version": base_version_no, "version": active["version_no"] + 1,
                    "config_hash": digest})
    db.commit()
    return {"version_id": version_id, "version_no": active["version_no"] + 1, "config_hash": digest}


def get_version(db: Session, *, agent_id: str, version_no: int) -> dict | None:
    """Agent-version detail (Section 12): the pinned immutable version row plus
    its ontology-binding child rows (with the enabled tool selection)."""
    row = db.execute(text(
        "SELECT id, version_no, name, description, config_hash, "
        "default_model_config_version_id, default_model_name, system_prompt, memory_settings, "
        "application_state_schema_version_id, change_note, prompt_generation_id, created_by, created_at "
        "FROM agent_versions WHERE agent_id = :id AND version_no = :vno"
    ), {"id": agent_id, "vno": version_no}).mappings().one_or_none()
    if row is None:
        return None
    result = dict(row)
    result["ontology_bindings"] = list(_child_tree(db, result["id"])["ontology_bindings"])
    return result


def restore_version(db: Session, *, actor_id: str, agent_id: str, source_version_no: int,
                    change_note: str | None = None) -> dict:
    """Section 12 restore: create N+1 as a byte-identical copy of the pinned
    version (same content, same config hash — child rows cloned) and
    CAS-activate it in one transaction.  The pinned version stays untouched;
    the restored version carries the fresh number max(version_no)+1."""
    agent = db.execute(text(
        "SELECT id, status, active_version_id FROM agents WHERE id = :id FOR UPDATE"
    ), {"id": agent_id}).mappings().one_or_none()
    if not agent or agent["status"] != "active":
        raise AgentConfigError("AGENT_NOT_FOUND")
    source = db.execute(text(
        "SELECT id, version_no, name, description, default_model_config_version_id, "
        "default_model_name, system_prompt, memory_settings, application_state_schema_version_id, "
        "config_hash, prompt_generation_id FROM agent_versions "
        "WHERE agent_id = :id AND version_no = :vno"
    ), {"id": agent_id, "vno": source_version_no}).mappings().one_or_none()
    if source is None:
        raise AgentConfigError("VERSION_NOT_FOUND")
    next_no = db.execute(text(
        "SELECT coalesce(max(version_no), 0) + 1 FROM agent_versions WHERE agent_id = :id"
    ), {"id": agent_id}).scalar_one()
    version_id = _new_id()
    db.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, description, "
        "default_model_config_version_id, default_model_name, system_prompt, memory_settings, "
        "application_state_schema_version_id, config_hash, change_note, prompt_generation_id, "
        "created_by, created_at) "
        "VALUES (:id, :agent, :rev, :name, :desc, :mvid, :mname, :sp, CAST(:mem AS json), "
        ":asv, :hash, :note, :pg, :actor, now())"
    ), {"id": version_id, "agent": agent_id, "rev": next_no,
        "name": source["name"], "desc": source["description"],
        "mvid": source["default_model_config_version_id"],
        "mname": source["default_model_name"], "sp": source["system_prompt"],
        "mem": _canonical(source["memory_settings"] or {}),
        "asv": source["application_state_schema_version_id"],
        "hash": source["config_hash"], "note": change_note,
        "pg": source["prompt_generation_id"], "actor": actor_id})
    _clone_child_rows(db, source["id"], version_id)
    result = db.execute(text(
        "UPDATE agents SET active_version_id = :vid, updated_at = now() "
        "WHERE id = :aid AND active_version_id = :old"
    ), {"vid": version_id, "aid": agent_id, "old": agent["active_version_id"]})
    if result.rowcount != 1:
        raise AgentConfigConflict("AGENT_VERSION_CONFLICT")
    _audit(db, actor_id=actor_id, agent_id=agent_id, operation="restore_version",
           payload={"source_version": source_version_no, "version": next_no,
                    "config_hash": source["config_hash"]})
    db.commit()
    return {"version_id": version_id, "version_no": next_no, "config_hash": source["config_hash"]}


def _agent_id_for_version(db: Session, agent_version_id: str) -> str:
    return db.execute(text(
        "SELECT agent_id FROM agent_versions WHERE id = :id"
    ), {"id": agent_version_id}).scalar_one()


def bind_external_tool(db: Session, *, actor_id: str, agent_version_id: str,
                       tool_connection_version_id: str, alias: str) -> dict:
    """Bind an approved tool-connection version to an Agent version under an
    alias (Task 7 derives the LangGraph tool name from the alias)."""
    approved = db.execute(text(
        "SELECT approval_status FROM tool_connection_versions WHERE id = :id"
    ), {"id": tool_connection_version_id}).scalar_one_or_none()
    if approved is None:
        raise AgentConfigError("EXTERNAL_TOOL_VERSION_NOT_APPROVED")
    if approved != "approved":
        raise AgentConfigError("EXTERNAL_TOOL_VERSION_NOT_APPROVED")
    binding_id = _new_id()
    try:
        db.execute(text(
            "INSERT INTO agent_external_tool_bindings "
            "(id, agent_version_id, tool_connection_version_id, alias, created_at) "
            "VALUES (:id, :av, :tcv, :alias, now())"
        ), {"id": binding_id, "av": agent_version_id, "tcv": tool_connection_version_id, "alias": alias})
    except IntegrityError:
        db.rollback()
        raise AgentConfigError("EXTERNAL_TOOL_ALIAS_TAKEN")
    _audit(db, actor_id=actor_id, agent_id=_agent_id_for_version(db, agent_version_id),
          operation="bind_external_tool", payload={"alias": alias, "version_id": tool_connection_version_id})
    db.commit()
    return {"id": binding_id, "agent_version_id": agent_version_id,
            "tool_connection_version_id": tool_connection_version_id, "alias": alias}


def unbind_external_tool(db: Session, *, actor_id: str, agent_version_id: str, alias: str) -> None:
    """Release a previously bound external-tool alias on an Agent version."""
    deleted = db.execute(text(
        "DELETE FROM agent_external_tool_bindings WHERE agent_version_id = :av AND alias = :alias RETURNING id"
    ), {"av": agent_version_id, "alias": alias}).scalar_one_or_none()
    if deleted is None:
        raise AgentConfigError("EXTERNAL_TOOL_BINDING_NOT_FOUND")
    _audit(db, actor_id=actor_id, agent_id=_agent_id_for_version(db, agent_version_id),
          operation="unbind_external_tool", payload={"alias": alias})
    db.commit()


def bind_skill(db: Session, *, actor_id: str, agent_version_id: str,
               skill_version_id: str, alias: str) -> dict:
    approved = db.execute(text(
        "SELECT approval_status FROM skill_versions WHERE id = :id"
    ), {"id": skill_version_id}).scalar_one_or_none()
    if approved is None or approved != "approved":
        raise AgentConfigError("SKILL_VERSION_NOT_APPROVED")
    binding_id = _new_id()
    try:
        db.execute(text(
            "INSERT INTO agent_skill_bindings (id, agent_version_id, skill_version_id, alias, created_at) "
            "VALUES (:id, :av, :sv, :alias, now())"
        ), {"id": binding_id, "av": agent_version_id, "sv": skill_version_id, "alias": alias})
    except IntegrityError:
        db.rollback()
        raise AgentConfigError("SKILL_ALIAS_TAKEN")
    _audit(db, actor_id=actor_id, agent_id=_agent_id_for_version(db, agent_version_id),
          operation="bind_skill", payload={"alias": alias, "version_id": skill_version_id})
    db.commit()
    return {"id": binding_id, "agent_version_id": agent_version_id,
            "skill_version_id": skill_version_id, "alias": alias}


def unbind_skill(db: Session, *, actor_id: str, agent_version_id: str, alias: str) -> None:
    deleted = db.execute(text(
        "DELETE FROM agent_skill_bindings WHERE agent_version_id = :av AND alias = :alias RETURNING id"
    ), {"av": agent_version_id, "alias": alias}).scalar_one_or_none()
    if deleted is None:
        raise AgentConfigError("SKILL_BINDING_NOT_FOUND")
    _audit(db, actor_id=actor_id, agent_id=_agent_id_for_version(db, agent_version_id),
          operation="unbind_skill", payload={"alias": alias})
    db.commit()


def list_external_tool_bindings(db: Session, *, agent_version_id: str) -> list[dict]:
    """Current external-tool bindings for one Agent version, joined with the
    connection/provider metadata the UI needs to render them (no listing
    function existed before this — bind/unbind were write-only)."""
    rows = db.execute(text(
        "SELECT aetb.id, aetb.alias, aetb.tool_connection_version_id, tcv.connection_id, "
        "tcv.version_no, tp.name AS provider_name, tp.kind AS provider_kind, "
        "tcv.approval_status, tcv.health_status "
        "FROM agent_external_tool_bindings aetb "
        "JOIN tool_connection_versions tcv ON tcv.id = aetb.tool_connection_version_id "
        "JOIN tool_connections tc ON tc.id = tcv.connection_id "
        "JOIN tool_providers tp ON tp.id = tc.provider_id "
        "WHERE aetb.agent_version_id = :id ORDER BY aetb.alias"
    ), {"id": agent_version_id}).mappings().all()
    return [dict(r) for r in rows]
