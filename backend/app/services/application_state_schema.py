"""Application state schema admin service (P3B-STATEADMIN).

Creates registries with immutable version 1, canonicalizes + hashes bounded
JSON Schemas, inserts N+1 inactive versions under `base_active_revision` CAS,
and activates (or archives via `target_revision=null`) with audit.  Versions
are always RESTRICT and never updated/deleted; there is no automatic
permissive schema.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

MAX_SCHEMA_DEPTH = 16
MAX_PROPERTY_COUNT = 128
MAX_SCHEMA_BYTES = 65536


class ApplicationStateSchemaError(Exception):
    """Rejected schema-registry operation."""


class ApplicationStateSchemaConflict(ApplicationStateSchemaError):
    """base_active_revision CAS mismatch or key/hash collision."""


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _check_bounds(node, depth: int, counter: list) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise ApplicationStateSchemaError("SCHEMA_BOMB depth exceeded")
    counter[0] += 1
    if counter[0] > MAX_PROPERTY_COUNT:
        raise ApplicationStateSchemaError("SCHEMA_BOMB node count exceeded")
    if not isinstance(node, dict):
        raise ApplicationStateSchemaError("SCHEMA_INVALID root must be an object")
    for key, value in node.items():
        if isinstance(value, (dict, list)):
            _check_bounds(value, depth + 1, counter)
        elif isinstance(value, str) and len(value) > 4096:
            raise ApplicationStateSchemaError("SCHEMA_BOMB string size exceeded")


def validate_bounded_json_schema(json_schema) -> str:
    """Validate + canonicalize a bounded JSON Schema; returns the canonical
    string.  Rejects depth/node/string bombs and non-object roots."""
    payload = _canonical(json_schema)
    if len(payload.encode()) > MAX_SCHEMA_BYTES:
        raise ApplicationStateSchemaError("SCHEMA_BOMB byte size exceeded")
    _check_bounds(json_schema, 0, [0])
    if json_schema.get("type") != "object":
        raise ApplicationStateSchemaError("SCHEMA_INVALID root type must be object")
    return payload


def _hash(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def _registry(db: Session, registry_id: str) -> dict | None:
    return db.execute(text(
        "SELECT id, application_key, status, active_version_id FROM "
        "application_state_schema_registries WHERE id = :id"
    ), {"id": registry_id}).mappings().one_or_none()


def _active_version(db: Session, registry_id: str) -> dict | None:
    return db.execute(text(
        "SELECT v.id, v.version_no FROM application_state_schema_versions v "
        "JOIN application_state_schema_registries r ON r.active_version_id = v.id "
        "WHERE r.id = :id AND r.status = 'active'"
    ), {"id": registry_id}).mappings().one_or_none()


def create_registry_with_version(db: Session, *, actor_id: str, application_key: str, json_schema) -> dict:
    existing = db.execute(text(
        "SELECT id, active_version_id FROM application_state_schema_registries "
        "WHERE application_key = :key"
    ), {"key": application_key}).mappings().one_or_none()
    canonical = validate_bounded_json_schema(json_schema)
    digest = _hash(canonical)
    if existing is not None:
        if existing["active_version_id"]:
            raise ApplicationStateSchemaConflict("APPLICATION_STATE_SCHEMA_KEY_EXISTS")
        raise ApplicationStateSchemaConflict("APPLICATION_STATE_SCHEMA_KEY_EXISTS")
    registry_id = _new_id()
    version_id = _new_id()
    db.execute(text(
        "INSERT INTO application_state_schema_registries "
        "(id, application_key, status, active_version_id, created_by, created_at, updated_at) "
        "VALUES (:id, :key, 'active', NULL, :actor, now(), now())"
    ), {"id": registry_id, "key": application_key, "actor": actor_id})
    db.execute(text(
        "INSERT INTO application_state_schema_versions "
        "(id, registry_id, version_no, json_schema, canonical_hash, created_by, created_at) "
        "VALUES (:id, :registry, 1, CAST(:schema AS json), :hash, :actor, now())"
    ), {"id": version_id, "registry": registry_id, "schema": canonical, "hash": digest, "actor": actor_id})
    db.execute(text(
        "UPDATE application_state_schema_registries SET active_version_id = :vid WHERE id = :rid"
    ), {"vid": version_id, "rid": registry_id})
    _audit(db, actor_id=actor_id, registry_id=registry_id, operation="create",
           payload={"application_key": application_key, "version": 1, "hash": digest})
    db.commit()
    return {"id": registry_id, "application_key": application_key, "status": "active",
            "active_version": {"id": version_id, "version_no": 1, "canonical_hash": digest}}


def create_schema_version(db: Session, *, actor_id: str, registry_id: str, base_active_revision: int, json_schema) -> dict:
    registry = _registry(db, registry_id)
    if not registry or registry["status"] != "active":
        raise ApplicationStateSchemaError("APPLICATION_STATE_SCHEMA_NOT_FOUND")
    active = _active_version(db, registry_id)
    if not active or active["version_no"] != base_active_revision:
        raise ApplicationStateSchemaConflict("APPLICATION_STATE_SCHEMA_REVISION_CONFLICT")
    canonical = validate_bounded_json_schema(json_schema)
    digest = _hash(canonical)
    max_no = db.execute(text(
        "SELECT COALESCE(max(version_no), 0) FROM application_state_schema_versions "
        "WHERE registry_id = :id"
    ), {"id": registry_id}).scalar_one()
    next_revision = max_no + 1
    version_id = _new_id()
    db.execute(text(
        "INSERT INTO application_state_schema_versions "
        "(id, registry_id, version_no, json_schema, canonical_hash, created_by, created_at) "
        "VALUES (:id, :registry, :rev, CAST(:schema AS json), :hash, :actor, now())"
    ), {"id": version_id, "registry": registry_id, "rev": next_revision,
        "schema": canonical, "hash": digest, "actor": actor_id})
    _audit(db, actor_id=actor_id, registry_id=registry_id, operation="version",
           payload={"base_version": base_active_revision, "version": next_revision, "hash": digest})
    db.commit()
    return {"id": version_id, "version_no": next_revision, "canonical_hash": digest, "status": "inactive"}


def activate_schema_version(db: Session, *, actor_id: str, registry_id: str, base_active_revision: int, target_revision: str | None) -> dict:
    registry = _registry(db, registry_id)
    if not registry or registry["status"] != "active":
        raise ApplicationStateSchemaError("APPLICATION_STATE_SCHEMA_NOT_FOUND")
    active = _active_version(db, registry_id)
    if not active or active["version_no"] != base_active_revision:
        raise ApplicationStateSchemaConflict("APPLICATION_STATE_SCHEMA_REVISION_CONFLICT")
    if target_revision is None:
        referenced = db.execute(text(
            "SELECT 1 FROM agent_versions WHERE application_state_schema_version_id = :vid LIMIT 1"
        ), {"vid": active["id"]}).scalar_one_or_none()
        if referenced:
            raise ApplicationStateSchemaError("APPLICATION_STATE_SCHEMA_REFERENCED")
        db.execute(text(
            "UPDATE application_state_schema_registries SET status = 'archived', "
            "active_version_id = NULL, updated_at = now() WHERE id = :id"
        ), {"id": registry_id})
        _audit(db, actor_id=actor_id, registry_id=registry_id, operation="archive", payload={"version": base_active_revision})
        db.commit()
        return {"id": registry_id, "status": "archived"}
    version = db.execute(text(
        "SELECT id, version_no FROM application_state_schema_versions "
        "WHERE id = :vid AND registry_id = :rid"
    ), {"vid": target_revision, "rid": registry_id}).mappings().one_or_none()
    if not version:
        raise ApplicationStateSchemaError("APPLICATION_STATE_SCHEMA_VERSION_NOT_FOUND")
    db.execute(text(
        "UPDATE application_state_schema_registries SET active_version_id = :vid, updated_at = now() "
        "WHERE id = :rid"
    ), {"vid": version["id"], "rid": registry_id})
    _audit(db, actor_id=actor_id, registry_id=registry_id, operation="activate",
           payload={"base_version": base_active_revision, "version": version["version_no"]})
    db.commit()
    return {"id": registry_id, "status": "active", "active_version_no": version["version_no"]}


def list_schema_registries(db: Session) -> list[dict]:
    rows = db.execute(text(
        "SELECT r.id, r.application_key, r.status, r.active_version_id, "
        "v.version_no AS active_version_no, v.canonical_hash, "
        "(SELECT count(*) FROM application_state_schema_versions sv WHERE sv.registry_id = r.id) AS versions_count "
        "FROM application_state_schema_registries r "
        "LEFT JOIN application_state_schema_versions v ON v.id = r.active_version_id "
        "ORDER BY r.created_at"
    )).mappings().all()
    return [dict(row) for row in rows]


def _audit(db: Session, *, actor_id: str, registry_id: str, operation: str, payload: dict) -> None:
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
        "corr": f"ass:{registry_id[-8:]}:{_new_id()[:8]}",
        "payload": json.dumps({"event_type": "application_state_schema", "operation": operation,
                               "registry_id": registry_id, "actor_id": actor_id, **payload}, sort_keys=True),
    })
