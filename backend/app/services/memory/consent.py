"""Per-revision memory consent, revocable (P6B-2a, Section 11: "Consent is
stored per revision and revocation tombstones all memories relying on that
consent basis"). Only a memory's LATEST (non-superseded) revision governs
whether it's currently tombstoned by a given consent revocation -- an
earlier revision's consent basis is historical, not authoritative."""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def _new_id() -> str:
    return str(uuid.uuid4())


def grant_consent(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                  consent_basis: str) -> str:
    consent_id = _new_id()
    db.execute(text(
        "INSERT INTO agent_memory_consents (id, security_domain_id, agent_id, user_id, consent_basis, granted_at) "
        "VALUES (:id, :d, :a, :u, :basis, now())"
    ), {"id": consent_id, "d": security_domain_id, "a": agent_id, "u": user_id, "basis": consent_basis})
    db.commit()
    return consent_id


def revoke_consent(db: Session, *, consent_id: str) -> int:
    db.execute(text(
        "UPDATE agent_memory_consents SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL"
    ), {"id": consent_id})
    dependent_memory_ids = db.execute(text(
        "SELECT r.memory_id FROM agent_memory_revisions r "
        "WHERE r.consent_id = :cid AND r.superseded_at IS NULL"
    ), {"cid": consent_id}).scalars().all()
    tombstoned = 0
    for memory_id in dependent_memory_ids:
        result = db.execute(text(
            "UPDATE agent_memories SET status = 'deleted', deleted_at = now(), updated_at = now() "
            "WHERE id = :id AND status != 'deleted'"
        ), {"id": memory_id})
        if result.rowcount:
            tombstoned += result.rowcount
            db.execute(text(
                "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
                "VALUES (:id, :mid, 'delete', 'pending', now())"
            ), {"id": _new_id(), "mid": memory_id})
    db.commit()
    return tombstoned
