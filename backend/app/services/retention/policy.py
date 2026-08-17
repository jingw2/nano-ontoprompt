"""Retention policy resolution and the per-domain epoch lock (P6A).

The epoch lock (`pg_advisory_xact_lock`, keyed by `hashtext(security_domain_id)`)
serializes policy activation, hold create/release, and every purge batch for
one domain, so a newly committed hold or policy can never race behind an
already-selected deletion. `security_domains` itself is immutable (see
`security_domains_immutable` trigger) — the epoch counter lives in the
separate `retention_epochs` table.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

# floor durations, in days, for every purge class this plan governs — a
# policy version's `rules` JSON may raise these but never lower them
# (validated at creation time in policy_admin.py, Task 4)
TABLE_MINIMUMS: dict[str, int] = {
    "application_state_snapshot.redact": 365,
    "clarification.delete": 1,
    "runtime_event.redact": 90,
    "message.redact": 90,
    "model_invocation.delete": 5,
    "node_execution.delete": 5,
    "checkpoint.delete": 7,
    "dispatch_outbox.delete": 30,
    "turn.delete": 7,
    "graph_index.delete": 7,
}


class RetentionPolicyError(Exception):
    """Rejected retention-policy operation."""


class RetentionPolicyConflict(RetentionPolicyError):
    """CAS mismatch on policy activation."""


def acquire_domain_lock(db: Session, security_domain_id: str) -> None:
    """Transaction-scoped advisory lock for one security domain. Must be
    called inside an open transaction (`with session.begin():` or an
    already-begun session) — the lock releases automatically on
    commit/rollback, never held across requests."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:domain))"), {"domain": security_domain_id})


def current_epoch(db: Session, security_domain_id: str) -> int:
    row = db.execute(text(
        "SELECT epoch FROM retention_epochs WHERE security_domain_id = :domain"
    ), {"domain": security_domain_id}).scalar_one_or_none()
    if row is None:
        raise RetentionPolicyError("RETENTION_DOMAIN_NOT_FOUND")
    return int(row)


def bump_epoch(db: Session, security_domain_id: str) -> int:
    """Increment and return the new epoch. Caller must hold the domain lock
    (`acquire_domain_lock`) in the same transaction first."""
    row = db.execute(text(
        "UPDATE retention_epochs SET epoch = epoch + 1, updated_at = now() "
        "WHERE security_domain_id = :domain RETURNING epoch"
    ), {"domain": security_domain_id}).scalar_one_or_none()
    if row is None:
        raise RetentionPolicyError("RETENTION_DOMAIN_NOT_FOUND")
    return int(row)


def resolve_active_duration(db: Session, security_domain_id: str, class_action: str) -> int:
    """`max(active_policy_rules.get(class_action, 0), TABLE_MINIMUMS[class_action])`,
    in days. A policy's rules may omit a class entirely (an older, narrower
    policy version) — that still floors to the table minimum, never to 0."""
    if class_action not in TABLE_MINIMUMS:
        raise RetentionPolicyError("RETENTION_CLASS_UNKNOWN")
    row = db.execute(text(
        "SELECT v.rules FROM retention_policies p "
        "JOIN retention_policy_versions v ON v.id = p.active_version_id "
        "WHERE p.security_domain_id = :domain"
    ), {"domain": security_domain_id}).scalar_one_or_none()
    active_days = int((row or {}).get(class_action, 0))
    return max(active_days, TABLE_MINIMUMS[class_action])


def _canonical_hash(rules: dict) -> str:
    canonical = json.dumps(rules, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_policy_version(db: Session, *, actor_id: str, security_domain_id: str, rules: dict) -> dict:
    for key, value in rules.items():
        if key not in TABLE_MINIMUMS:
            raise RetentionPolicyError("RETENTION_CLASS_UNKNOWN")
        if int(value) < TABLE_MINIMUMS[key]:
            raise RetentionPolicyError("RETENTION_MINIMUM_VIOLATION")

    policy_id = db.execute(text(
        "SELECT id FROM retention_policies WHERE security_domain_id = :domain"
    ), {"domain": security_domain_id}).scalar_one_or_none()
    if policy_id is None:
        raise RetentionPolicyError("RETENTION_DOMAIN_NOT_FOUND")

    next_version = db.execute(text(
        "SELECT COALESCE(MAX(version_no), 0) + 1 FROM retention_policy_versions WHERE policy_id = :policy"
    ), {"policy": policy_id}).scalar_one()

    version_id = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO retention_policy_versions "
        "(id, policy_id, version_no, rules, canonical_hash, effective_at, status, created_by, created_at) "
        "VALUES (:id, :policy, :vno, CAST(:rules AS json), :hash, now(), 'pending', :actor, now())"
    ), {"id": version_id, "policy": policy_id, "vno": next_version,
        "rules": json.dumps(rules), "hash": _canonical_hash(rules), "actor": actor_id})
    db.commit()
    return {"id": version_id, "policy_id": policy_id, "version_no": next_version, "status": "pending"}


def activate_policy_version(db: Session, *, actor_id: str, security_domain_id: str,
                            version_id: str, base_epoch: int) -> dict:
    """Relies on the Session's autobegin (the first `db.execute()` below
    opens the transaction implicitly) plus an explicit final `db.commit()` —
    the same pattern `application_state_schema.py:activate_schema_version`
    uses. Do NOT wrap this in `with db.begin():`: SQLAlchemy raises
    "already begun" if autobegin has already opened a transaction on this
    session, which it always has by the time a FastAPI route handler calls
    a second service function, or even on this function's own first
    statement — see acquire_domain_lock below."""
    acquire_domain_lock(db, security_domain_id)
    if current_epoch(db, security_domain_id) != base_epoch:
        raise RetentionPolicyConflict("RETENTION_EPOCH_CONFLICT")

    policy_id = db.execute(text(
        "SELECT id FROM retention_policies WHERE security_domain_id = :domain"
    ), {"domain": security_domain_id}).scalar_one_or_none()
    version = db.execute(text(
        "SELECT id FROM retention_policy_versions WHERE id = :id AND policy_id = :policy"
    ), {"id": version_id, "policy": policy_id}).scalar_one_or_none()
    if policy_id is None or version is None:
        raise RetentionPolicyError("RETENTION_VERSION_NOT_FOUND")

    db.execute(text(
        "UPDATE retention_policy_versions SET status = 'superseded' "
        "WHERE policy_id = :policy AND status = 'active'"
    ), {"policy": policy_id})
    db.execute(text(
        "UPDATE retention_policy_versions SET status = 'active' WHERE id = :id"
    ), {"id": version_id})
    db.execute(text(
        "UPDATE retention_policies SET active_version_id = :vid, updated_at = now() WHERE id = :id"
    ), {"vid": version_id, "id": policy_id})
    new_epoch = bump_epoch(db, security_domain_id)
    db.commit()
    return {"policy_id": policy_id, "active_version_id": version_id, "epoch": new_epoch}


def list_policy_versions(db: Session, limit: int = 100) -> list[dict]:
    rows = db.execute(text(
        "SELECT v.id, v.policy_id, v.version_no, v.status, v.rules "
        "FROM retention_policy_versions v ORDER BY v.created_at DESC LIMIT :lim"
    ), {"lim": limit}).mappings().all()
    return [dict(r) for r in rows]
