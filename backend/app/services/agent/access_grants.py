"""Agent access grants (follow-up Section 12 completion).

Owner invariant + no self-escalation + CAS: the Agent owner always holds full
authority and is never granted a row; the acting user can neither grant to
themselves nor revise/revoke their own grant; every revision/revoke is CAS on
the grant `revision`.  Capabilities are the closed vocabulary
`discover|run|view_config|edit`.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

AGENT_GRANT_CAPABILITIES = frozenset({"discover", "run", "view_config", "edit"})


class AgentAccessGrantError(Exception):
    """Stable error code (route maps it to 422/404)."""


class AgentAccessGrantConflict(AgentAccessGrantError):
    """CAS conflict (route maps it to 409)."""


def _new_id() -> str:
    return str(uuid.uuid4())


def _canonical(value) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _validate_capabilities(capabilities) -> list[str]:
    if not isinstance(capabilities, list) or not capabilities:
        raise AgentAccessGrantError("AGENT_GRANT_CAPABILITIES_INVALID")
    caps = list(dict.fromkeys(str(c) for c in capabilities))
    if not set(caps) <= AGENT_GRANT_CAPABILITIES:
        raise AgentAccessGrantError("AGENT_GRANT_CAPABILITIES_INVALID")
    return caps


def _owner_id(db: Session, agent_id: str) -> str | None:
    row = db.execute(text("SELECT owner_id FROM agents WHERE id = :id"),
                     {"id": agent_id}).mappings().one_or_none()
    return row["owner_id"] if row else None


def _grant_out(row) -> dict:
    out = dict(row)
    out["capabilities"] = out.get("capabilities") or []
    return out


def list_agent_access_grants(db: Session, *, agent_id: str) -> dict:
    rows = db.execute(text(
        "SELECT id, agent_id, user_id, capabilities, revision, status, created_by, "
        "created_at, updated_at FROM agent_access_grants "
        "WHERE agent_id = :id AND status = 'active' ORDER BY created_at"
    ), {"id": agent_id}).mappings().all()
    return {"items": [_grant_out(dict(r)) for r in rows], "next_cursor": None, "has_more": False}


def create_agent_access_grant(db: Session, *, actor_id: str, agent_id: str,
                              user_id: str, capabilities) -> dict:
    """Create a grant (201).  Rejects owner grants (owner invariant) and
    self-grants (no self-escalation)."""
    caps = _validate_capabilities(capabilities)
    owner = _owner_id(db, agent_id)
    if owner is None:
        raise AgentAccessGrantError("AGENT_NOT_FOUND")
    if user_id == owner:
        raise AgentAccessGrantError("OWNER_INVARIANT")
    if user_id == actor_id:
        raise AgentAccessGrantError("SELF_ESCALATION")
    grant_id = _new_id()
    db.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, "
        "status, created_by, created_at, updated_at) "
        "VALUES (:id, :agent, :uid, CAST(:caps AS json), 1, 'active', :actor, now(), now())"
    ), {"id": grant_id, "agent": agent_id, "uid": user_id, "caps": _canonical(caps),
        "actor": actor_id})
    db.commit()
    return _grant_out({
        "id": grant_id, "agent_id": agent_id, "user_id": user_id, "capabilities": caps,
        "revision": 1, "status": "active", "created_by": actor_id,
    })


def revise_agent_access_grant(db: Session, *, actor_id: str, grant_id: str,
                              base_revision: int, capabilities) -> dict:
    """CAS revise (201).  The actor may never revise their own grant."""
    caps = _validate_capabilities(capabilities)
    row = db.execute(text(
        "SELECT id, agent_id, user_id, revision, status FROM agent_access_grants "
        "WHERE id = :id FOR UPDATE"
    ), {"id": grant_id}).mappings().one_or_none()
    if row is None or row["status"] != "active":
        raise AgentAccessGrantError("GRANT_NOT_FOUND")
    if row["user_id"] == actor_id:
        raise AgentAccessGrantError("SELF_ESCALATION")
    if row["revision"] != base_revision:
        raise AgentAccessGrantConflict("AGENT_GRANT_CONFLICT")
    result = db.execute(text(
        "UPDATE agent_access_grants SET capabilities = CAST(:caps AS json), "
        "revision = revision + 1, updated_at = now() "
        "WHERE id = :id AND revision = :rev AND status = 'active'"
    ), {"caps": _canonical(caps), "id": grant_id, "rev": base_revision})
    if result.rowcount != 1:
        raise AgentAccessGrantConflict("AGENT_GRANT_CONFLICT")
    db.commit()
    return _grant_out({
        "id": grant_id, "agent_id": row["agent_id"], "user_id": row["user_id"],
        "capabilities": caps, "revision": base_revision + 1, "status": "active",
    })


def revoke_agent_access_grant(db: Session, *, actor_id: str, grant_id: str,
                              base_revision: int) -> dict:
    """CAS revoke (200).  The actor may never revoke their own grant."""
    row = db.execute(text(
        "SELECT id, agent_id, user_id, revision, status FROM agent_access_grants "
        "WHERE id = :id FOR UPDATE"
    ), {"id": grant_id}).mappings().one_or_none()
    if row is None or row["status"] != "active":
        raise AgentAccessGrantError("GRANT_NOT_FOUND")
    if row["user_id"] == actor_id:
        raise AgentAccessGrantError("SELF_ESCALATION")
    if row["revision"] != base_revision:
        raise AgentAccessGrantConflict("AGENT_GRANT_CONFLICT")
    result = db.execute(text(
        "UPDATE agent_access_grants SET status = 'revoked', revision = revision + 1, "
        "updated_at = now() WHERE id = :id AND revision = :rev AND status = 'active'"
    ), {"id": grant_id, "rev": base_revision})
    if result.rowcount != 1:
        raise AgentAccessGrantConflict("AGENT_GRANT_CONFLICT")
    db.commit()
    return _grant_out({
        "id": grant_id, "agent_id": row["agent_id"], "user_id": row["user_id"],
        "capabilities": [], "revision": base_revision + 1, "status": "revoked",
    })
