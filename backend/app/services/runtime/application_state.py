"""Agent application-state service (P3B-STATEAUDIT).

Schema-validated snapshots with `(base_revision, base_hash)` CAS against the
pinned ApplicationStateSchemaVersion.  The registered schema permits only the
declared variable vocabulary; arbitrary JSON is rejected.  The snapshot is
canonicalized and hashed before insert.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


class ApplicationStateError(Exception):
    """Rejected application-state operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _canonical(state: dict) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(state: dict) -> str:
    return hashlib.sha256(_canonical(state).encode("utf-8")).hexdigest()


def _pinned_schema(db: Session, session_id: str) -> dict:
    """The exact schema tuple pinned by the session's agent active version."""
    row = db.execute(text(
        "SELECT s.id, s.version_no AS revision, s.canonical_hash AS schema_hash, s.json_schema "
        "FROM agent_sessions sess "
        "JOIN agents a ON a.id = sess.agent_id "
        "JOIN agent_versions v ON v.id = a.active_version_id "
        "JOIN application_state_schema_versions s ON s.id = v.application_state_schema_version_id "
        "WHERE sess.id = :sid"
    ), {"sid": session_id}).mappings().one_or_none()
    if row is None:
        raise ApplicationStateError("SCHEMA_UNAVAILABLE")
    return dict(row)


def _validate(state: dict, json_schema: dict) -> None:
    """Bounded structural validation: only keys declared in the schema's
    `properties` are permitted, each with the declared JSON type.  Unknown
    keys or wrong types fail closed (APPLICATION_STATE_INVALID)."""
    properties = (json_schema or {}).get("properties", {})
    required = (json_schema or {}).get("required", [])
    for key in state:
        if key not in properties:
            raise ApplicationStateError("APPLICATION_STATE_INVALID")
        declared = properties[key]
        if isinstance(state[key], list):
            if declared.get("type") != "array":
                raise ApplicationStateError("APPLICATION_STATE_INVALID")
            item_type = (declared.get("items") or {}).get("type")
            if item_type and item_type == "string" and not all(isinstance(i, str) for i in state[key]):
                raise ApplicationStateError("APPLICATION_STATE_INVALID")
        elif declared.get("type") == "string" and not isinstance(state[key], str):
            raise ApplicationStateError("APPLICATION_STATE_INVALID")
        elif declared.get("type") == "integer" and not isinstance(state[key], int):
            raise ApplicationStateError("APPLICATION_STATE_INVALID")
        elif declared.get("type") == "number" and not isinstance(state[key], (int, float)):
            raise ApplicationStateError("APPLICATION_STATE_INVALID")
        elif declared.get("type") == "boolean" and not isinstance(state[key], bool):
            raise ApplicationStateError("APPLICATION_STATE_INVALID")
    for key in required:
        if key not in state:
            raise ApplicationStateError("APPLICATION_STATE_INVALID")


def get_snapshot(db: Session, *, session_id: str) -> dict:
    row = db.execute(text(
        "SELECT id, session_id, revision, canonical_hash, schema_version_id, schema_hash, "
        "canonical_bytes, created_at FROM agent_application_state_snapshots "
        "WHERE session_id = :sid ORDER BY revision DESC LIMIT 1"
    ), {"sid": session_id}).mappings().one_or_none()
    if row is None:
        pinned = _pinned_schema(db, session_id)
        return {
            "session_id": session_id, "revision": 0, "hash": "",
            "schema_version_id": pinned["id"], "schema_revision": pinned["revision"],
            "schema_hash": pinned["schema_hash"], "state": {}, "created_at": None,
        }
    return {
        "session_id": row["session_id"], "revision": row["revision"], "hash": row["canonical_hash"],
        "schema_version_id": row["schema_version_id"], "schema_revision": 0,
        "schema_hash": row["schema_hash"], "state": json.loads(bytes(row["canonical_bytes"]).decode("utf-8")),
        "created_at": row["created_at"],
    }


def patch_snapshot(db: Session, *, session_id: str, actor_id: str,
                   base_revision: int, base_hash: str, patch: dict) -> dict:
    """CAS `(base_revision, base_hash)` -> next revision; schema-validated."""
    pinned = _pinned_schema(db, session_id)
    current = db.execute(text(
        "SELECT revision, canonical_hash, canonical_bytes FROM agent_application_state_snapshots "
        "WHERE session_id = :sid ORDER BY revision DESC LIMIT 1"
    ), {"sid": session_id}).mappings().one_or_none()
    if current is None:
        if base_revision != 0 or base_hash != "":
            raise ApplicationStateError("APPLICATION_STATE_CONFLICT")
        base_state: dict = {}
    else:
        if current["revision"] != base_revision or current["canonical_hash"] != base_hash:
            raise ApplicationStateError("APPLICATION_STATE_CONFLICT")
        base_state = json.loads(bytes(current["canonical_bytes"]).decode("utf-8"))
    merged = {**base_state, **patch}
    _validate(merged, pinned["json_schema"])
    new_revision = base_revision + 1
    new_hash = _hash(merged)
    db.execute(text(
        "INSERT INTO agent_application_state_snapshots "
        "(id, session_id, revision, parent_revision, schema_version_id, schema_hash, "
        "canonical_bytes, canonical_hash, actor_user_id, sources, selected_instances, created_at) "
        "VALUES (:id, :sid, :rev, :parent, :svid, :shash, :bytes, :hash, :actor, '[]'::json, '[]'::json, now())"
    ), {"id": _new_id(), "sid": session_id, "rev": new_revision, "parent": base_revision,
        "svid": pinned["id"], "shash": pinned["schema_hash"],
        "bytes": _canonical(merged).encode("utf-8"), "hash": new_hash, "actor": actor_id})
    db.commit()
    return {
        "session_id": session_id, "revision": new_revision, "hash": new_hash,
        "schema_version_id": pinned["id"], "schema_revision": pinned["revision"],
        "schema_hash": pinned["schema_hash"], "state": merged, "created_at": _now(),
    }
