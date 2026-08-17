"""Legal holds (P6A): an active hold blocks payload redaction, FK nulling,
and purge for its scoped dependency closure. Create/release both bump the
domain epoch so an in-flight purge batch selected before the hold committed
is forced to re-check eligibility on its next batch."""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.retention.policy import acquire_domain_lock, bump_epoch

SCOPE_TYPES = ("subject", "session", "turn", "object")


class RetentionHoldError(Exception):
    """Rejected retention-hold operation."""


def create_hold(db: Session, *, actor_id: str, security_domain_id: str,
                scope_type: str, scope_id: str, reason: str) -> dict:
    """See the docstring on activate_policy_version in policy.py for why
    this relies on Session autobegin + explicit db.commit() rather than
    `with db.begin():`."""
    if scope_type not in SCOPE_TYPES:
        raise RetentionHoldError("HOLD_SCOPE_INVALID")
    hold_id = str(uuid.uuid4())
    acquire_domain_lock(db, security_domain_id)
    db.execute(text(
        "INSERT INTO retention_holds "
        "(id, security_domain_id, scope_type, scope_id, reason, issued_by, issued_at) "
        "VALUES (:id, :domain, :stype, :sid, :reason, :actor, now())"
    ), {"id": hold_id, "domain": security_domain_id, "stype": scope_type,
        "sid": scope_id, "reason": reason, "actor": actor_id})
    bump_epoch(db, security_domain_id)
    db.commit()
    row = db.execute(text(
        "SELECT issued_at FROM retention_holds WHERE id = :id"
    ), {"id": hold_id}).scalar_one()
    return {"id": hold_id, "scope_type": scope_type, "scope_id": scope_id, "issued_at": row}


def release_hold(db: Session, *, actor_id: str, security_domain_id: str, hold_id: str) -> dict:
    acquire_domain_lock(db, security_domain_id)
    updated = db.execute(text(
        "UPDATE retention_holds SET released_by = :actor, released_at = now() "
        "WHERE id = :id AND security_domain_id = :domain AND released_at IS NULL "
        "RETURNING id"
    ), {"actor": actor_id, "id": hold_id, "domain": security_domain_id}).scalar_one_or_none()
    if updated is None:
        raise RetentionHoldError("HOLD_NOT_FOUND")
    bump_epoch(db, security_domain_id)
    db.commit()
    return {"id": hold_id, "released": True}


def is_held(db: Session, security_domain_id: str, scope_type: str, scope_id: str) -> bool:
    return bool(db.execute(text(
        "SELECT 1 FROM retention_holds WHERE security_domain_id = :domain "
        "AND scope_type = :stype AND scope_id = :sid AND released_at IS NULL"
    ), {"domain": security_domain_id, "stype": scope_type, "sid": scope_id}).scalar_one_or_none())
