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
from sqlalchemy.orm import Session

AGENT_CAPABILITIES = ("discover", "run", "view_config", "edit", "view_audit")


class AgentConfigError(Exception):
    """Rejected agent-configuration operation."""


class AgentConfigConflict(AgentConfigError):
    """base_version_no CAS mismatch (concurrent save)."""


def _new_id() -> str:
    return str(uuid.uuid4())


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


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
        "SELECT ontology_id, capabilities, allowlists FROM agent_ontology_bindings "
        "WHERE agent_version_id = :id ORDER BY ontology_id"
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
        "ontology_bindings": [dict(b) for b in bindings],
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
) -> dict:
    """One transaction: Agent + AgentVersion v1 + owner grant + audit."""
    _verify_model_version(db, default_model_config_version_id, default_model_name)
    agent_id = _new_id()
    version_id = _new_id()
    digest = config_hash(
        name=name, description=description,
        default_model_config_version_id=default_model_config_version_id,
        default_model_name=default_model_name, system_prompt=system_prompt,
        memory_settings=memory_settings,
        application_state_schema_version_id=application_state_schema_version_id,
        prompt_generation_id=None,
        child_tree={"ontology_bindings": [], "external_tool_bindings": [], "retrieval_sources": []},
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
    for table, cols in (
        ("agent_ontology_bindings", ("ontology_id", "capabilities", "allowlists")),
        ("agent_external_tool_bindings", ("tool_connection_version_id", "alias")),
    ):
        rows = db.execute(text(
            f"SELECT {', '.join(cols)} FROM {table} WHERE agent_version_id = :id"
        ), {"id": source_version_id}).mappings().all()
        for row in rows:
            db.execute(text(
                f"INSERT INTO {table} (id, agent_version_id, {', '.join(cols)}) "
                f"VALUES (:id, :av, {', '.join(':' + c for c in cols)})"
            ), {"id": _new_id(), "av": target_version_id, **{c: row[c] for c in cols}})
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


def save_basic_version(
    db: Session, *, actor_id: str, agent_id: str, base_version_no: int,
    name: str, description: str | None, default_model_config_version_id: str,
    default_model_name: str, system_prompt: str | None, memory_settings: dict,
    application_state_schema_version_id: str, change_note: str | None = None,
    prompt_generation_id: str | None = None,
) -> dict:
    """Lock the Agent, clone the tree, apply the complete Basic patch, hash,
    insert N+1 and CAS-activate.  Old versions stay byte-identical."""
    _verify_model_version(db, default_model_config_version_id, default_model_name)
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
