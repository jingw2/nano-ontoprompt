"""Ontology data grant service (P2B-DATAGRANT).

Delegated-governance authorization, immutable revisions, revoke CAS and
audit.  Capabilities are intersected with the principal's exact role ceiling;
the row policy must compile under the restricted DSL or the grant is rejected.
Revocation takes effect immediately; every Section 12 data-grant method
reduces (never expands) current authority.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.agent.policy import (
    DATA_CAPABILITIES,
    PolicyDslInvalid,
    compile_row_policy,
    role_data_capability_ceiling,
)


class DataGrantError(Exception):
    """Rejected data-grant operation (authorization/CAS/validation)."""


class DataGrantRevisionConflict(DataGrantError):
    """base_revision CAS mismatch."""


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def has_data_grant_authority(db: Session, actor_id: str, ontology_id: str) -> bool:
    """Admin, or an active project `edit` grant on the ontology (delegated
    data-governance authority)."""
    role = db.execute(text(
        "SELECT role FROM users WHERE id = :id"
    ), {"id": actor_id}).scalar_one_or_none()
    if role == "admin":
        return True
    grant = db.execute(text(
        "SELECT 1 FROM ontology_project_access_grants "
        "WHERE ontology_id = :oid AND user_id = :uid AND status = 'active' "
        "AND capabilities::text LIKE '%\"edit\"%' LIMIT 1"
    ), {"oid": ontology_id, "uid": actor_id}).scalar_one_or_none()
    return grant is not None


def _audit(db: Session, *, ontology_id: str, actor_id: str, operation: str, payload: dict) -> None:
    db.execute(text(
        "INSERT INTO governance_audit_outbox "
        "(id, security_domain_id, correlation_id, payload, state, attempts, created_at, updated_at) "
        "VALUES (:id, :domain, :corr, CAST(:payload AS jsonb), 'pending', 0, now(), now())"
    ), {
        "id": _new_id(),
        "domain": db.execute(text(
            "SELECT security_domain_id FROM ontology_projects WHERE id = :id"
        ), {"id": ontology_id}).scalar_one(),
        "corr": f"dg:{ontology_id[-8:]}:{_new_id()[:8]}",
        "payload": __import__("json").dumps({
            "event_type": "ontology_data_grant",
            "operation": operation,
            "ontology_id": ontology_id,
            "actor_id": actor_id,
            **payload,
        }, sort_keys=True),
    })


def _apply_ceiling(db: Session, user_id: str, capabilities: list) -> list:
    role = db.execute(text("SELECT role FROM users WHERE id = :id"), {"id": user_id}).scalar_one()
    ceiling = role_data_capability_ceiling(role)
    unknown = set(capabilities) - DATA_CAPABILITIES
    if unknown:
        raise DataGrantError(f"DATA_GRANT_UNKNOWN_CAPABILITY {sorted(unknown)}")
    return sorted(set(capabilities) & ceiling)


def create_data_grant(
    db: Session, *, actor_id: str, user_id: str, ontology_id: str,
    capabilities: list, entity_allowlist=None, property_allowlist=None,
    relation_allowlist=None, action_allowlist=None, policy_version="restricted-policy-dsl-v1",
    row_policy=None, valid_from=None, valid_until=None,
) -> dict:
    if not has_data_grant_authority(db, actor_id, ontology_id):
        raise DataGrantError("DATA_GRANT_FORBIDDEN")
    capped = _apply_ceiling(db, user_id, capabilities)
    if not capped:
        raise DataGrantError("DATA_GRANT_EMPTY_CAPABILITIES")
    try:
        compiled = compile_row_policy(row_policy) if row_policy is not None else None
    except PolicyDslInvalid as exc:
        raise DataGrantError(f"DATA_GRANT_POLICY_INVALID {exc}") from exc
    grant_id = _new_id()
    db.execute(text(
        "INSERT INTO ontology_data_grants "
        "(id, ontology_id, user_id, capabilities, entity_allowlist, property_allowlist, "
        " relation_allowlist, action_allowlist, policy_version, row_policy, revision, status, "
        " valid_from, valid_until, created_by, created_at, updated_at) "
        "VALUES (:id, :oid, :uid, CAST(:caps AS json), :ea, :pa, :ra, :aa, :pv, "
        " CAST(:rp AS json), 1, 'active', :vf, :vu, :actor, now(), now())"
    ), {
        "id": grant_id, "oid": ontology_id, "uid": user_id, "caps": __import__("json").dumps(capped),
        "ea": entity_allowlist, "pa": property_allowlist, "ra": relation_allowlist, "aa": action_allowlist,
        "pv": policy_version, "rp": __import__("json").dumps(compiled) if compiled is not None else None,
        "vf": valid_from, "vu": valid_until, "actor": actor_id,
    })
    _audit(db, ontology_id=ontology_id, actor_id=actor_id, operation="create", payload={"grant_id": grant_id, "user_id": user_id})
    db.commit()
    return {"id": grant_id, "ontology_id": ontology_id, "user_id": user_id,
            "capabilities": capped, "revision": 1, "status": "active"}


def _current_revision(db: Session, grant_id: str) -> dict | None:
    return db.execute(text(
        "SELECT id, ontology_id, user_id, revision, status FROM ontology_data_grants "
        "WHERE id = :id AND status = 'active' ORDER BY revision DESC LIMIT 1"
    ), {"id": grant_id}).mappings().one_or_none()


def revise_data_grant(
    db: Session, *, actor_id: str, grant_id: str, base_revision: int,
    capabilities=None, row_policy=None, valid_until=None,
) -> dict:
    current = _current_revision(db, grant_id)
    if not current:
        raise DataGrantError("DATA_GRANT_NOT_FOUND")
    if not has_data_grant_authority(db, actor_id, current["ontology_id"]):
        raise DataGrantError("DATA_GRANT_FORBIDDEN")
    if base_revision != current["revision"]:
        raise DataGrantRevisionConflict("DATA_GRANT_REVISION_CONFLICT")
    row = db.execute(text(
        "SELECT capabilities, entity_allowlist, property_allowlist, relation_allowlist, "
        "action_allowlist, policy_version, row_policy, valid_from, valid_until "
        "FROM ontology_data_grants WHERE id = :id AND status = 'active' ORDER BY revision DESC LIMIT 1"
    ), {"id": grant_id}).mappings().one()
    caps = row["capabilities"] if capabilities is None else _apply_ceiling(db, current["user_id"], capabilities)
    try:
        compiled = compile_row_policy(row_policy) if row_policy is not None else row["row_policy"]
    except PolicyDslInvalid as exc:
        raise DataGrantError(f"DATA_GRANT_POLICY_INVALID {exc}") from exc
    next_revision = current["revision"] + 1
    new_id = _new_id()
    db.execute(text(
        "INSERT INTO ontology_data_grants "
        "(id, ontology_id, user_id, capabilities, entity_allowlist, property_allowlist, "
        " relation_allowlist, action_allowlist, policy_version, row_policy, revision, status, "
        " valid_from, valid_until, created_by, created_at, updated_at) "
        "VALUES (:id, :oid, :uid, CAST(:caps AS json), :ea, :pa, :ra, :aa, :pv, "
        " CAST(:rp AS json), :rev, 'active', :vf, :vu, :actor, now(), now())"
    ), {
        "id": new_id, "oid": current["ontology_id"], "uid": current["user_id"],
        "caps": __import__("json").dumps(caps),
        "ea": row["entity_allowlist"], "pa": row["property_allowlist"], "ra": row["relation_allowlist"],
        "aa": row["action_allowlist"], "pv": row["policy_version"],
        "rp": __import__("json").dumps(compiled) if compiled is not None else None,
        "rev": next_revision, "vf": row["valid_from"],
        "vu": valid_until if valid_until is not None else row["valid_until"], "actor": actor_id,
    })
    db.execute(text(
        "UPDATE ontology_data_grants SET status = 'revoked', updated_at = now() "
        "WHERE id = :id AND status = 'active'"
    ), {"id": grant_id})
    _audit(db, ontology_id=current["ontology_id"], actor_id=actor_id, operation="revise",
           payload={"grant_id": grant_id, "revision": next_revision})
    db.commit()
    return {"id": new_id, "ontology_id": current["ontology_id"], "user_id": current["user_id"],
            "capabilities": caps, "revision": next_revision, "status": "active"}


def revoke_data_grant(db: Session, *, actor_id: str, grant_id: str, base_revision: int, reason: str) -> dict:
    current = _current_revision(db, grant_id)
    if not current:
        raise DataGrantError("DATA_GRANT_NOT_FOUND")
    if not has_data_grant_authority(db, actor_id, current["ontology_id"]):
        raise DataGrantError("DATA_GRANT_FORBIDDEN")
    if base_revision != current["revision"]:
        raise DataGrantRevisionConflict("DATA_GRANT_REVISION_CONFLICT")
    db.execute(text(
        "UPDATE ontology_data_grants SET status = 'revoked', revoked_by = :revoker, updated_at = now() "
        "WHERE id = :id AND status = 'active'"
    ), {"id": grant_id, "revoker": actor_id})
    _audit(db, ontology_id=current["ontology_id"], actor_id=actor_id, operation="revoke",
           payload={"grant_id": grant_id, "revision": current["revision"], "reason": reason})
    db.commit()
    return {"id": grant_id, "ontology_id": current["ontology_id"], "status": "revoked"}


def list_data_grants(db: Session, *, actor_id: str) -> list[dict]:
    role = db.execute(text("SELECT role FROM users WHERE id = :id"), {"id": actor_id}).scalar_one()
    if role == "admin":
        rows = db.execute(text(
            "SELECT id, ontology_id, user_id, capabilities, policy_version, revision, status, "
            "valid_from, valid_until, created_at FROM ontology_data_grants "
            "WHERE status = 'active' ORDER BY created_at DESC"
        )).mappings().all()
    else:
        rows = db.execute(text(
            "SELECT g.id, g.ontology_id, g.user_id, g.capabilities, g.policy_version, g.revision, "
            "g.status, g.valid_from, g.valid_until, g.created_at "
            "FROM ontology_data_grants g "
            "JOIN ontology_project_access_grants p ON p.ontology_id = g.ontology_id "
            "AND p.user_id = :uid AND p.status = 'active' AND p.capabilities::text LIKE '%\"edit\"%' "
            "WHERE g.status = 'active' ORDER BY g.created_at DESC"
        ), {"uid": actor_id}).mappings().all()
    return [dict(row) for row in rows]
