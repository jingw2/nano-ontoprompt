"""Memory inspection/correction/deletion (P6B-3, Section 12.1).

Self-service surface over the already-merged long-term memory write path
(P6B-2a) and recall path (P6B-2b): a user inspects, confirms/rejects,
corrects, deletes, and resolves conflicts among their OWN memories.
Authorization is always scoped by user_id -- never an agent-operator
access-grant check, matching the spec's "consented inspection" framing.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


class MemoryConsentRequiredError(Exception):
    """MEMORY_CONSENT_REQUIRED: a confirm action was attempted without
    the caller's explicit affirmative consent flag."""


class MemoryConflictError(Exception):
    """MEMORY_CONFLICT: an action was attempted on a memory that is
    currently in an open conflict and must be resolved first."""


def list_memories(db: Session, *, user_id: str, agent_id: str, status: str | None = None) -> list[dict]:
    query = (
        "SELECT id, subject_key, predicate, display_text, confidence, sensitivity, status, "
        "consent_basis, created_at, updated_at FROM agent_memories "
        "WHERE user_id = :u AND agent_id = :a AND status != 'deleted'"
    )
    params = {"u": user_id, "a": agent_id}
    if status is not None:
        query += " AND status = :status"
        params["status"] = status
    query += " ORDER BY updated_at DESC"
    rows = db.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]


def _embedding_status(db: Session, *, memory_id: str, embedding_model_version: str | None) -> str:
    """The spec's "reconciliation state" surfaced in the inspection drawer --
    derived read-only from already-authoritative SQL columns, not a new
    Chroma-querying reconciliation job (no such job exists anywhere in this
    codebase to build on; querying live Chroma state from a request handler
    would also violate the "SQL is authoritative, every recall hit is
    SQL-refetched" invariant this whole memory subsystem is built on)."""
    pending = db.execute(text(
        "SELECT 1 FROM agent_memory_vector_outbox WHERE memory_id = :id AND state = 'pending' "
        "LIMIT 1"
    ), {"id": memory_id}).scalar_one_or_none()
    if pending:
        return "pending"
    from app.services.memory.vector_store import MEMORY_EMBEDDING_MODEL_VERSION
    if embedding_model_version == MEMORY_EMBEDDING_MODEL_VERSION:
        return "current"
    return "never_embedded"


def get_memory(db: Session, *, user_id: str, memory_id: str) -> dict | None:
    row = db.execute(text(
        "SELECT id, subject_key, predicate, canonical_value, display_text, confidence, "
        "sensitivity, status, consent_basis, agent_id, embedding_model_version, "
        "created_at, updated_at "
        "FROM agent_memories WHERE id = :id AND user_id = :u"
    ), {"id": memory_id, "u": user_id}).mappings().one_or_none()
    if row is None:
        return None
    result = dict(row)
    result["embedding_status"] = _embedding_status(
        db, memory_id=memory_id, embedding_model_version=row["embedding_model_version"])
    revisions = db.execute(text(
        "SELECT revision_no, display_text, confidence, consent_basis, created_at, superseded_at "
        "FROM agent_memory_revisions WHERE memory_id = :id ORDER BY revision_no DESC"
    ), {"id": memory_id}).mappings().all()
    result["revisions"] = [dict(r) for r in revisions]
    result["conflict"] = None
    if row["status"] == "conflicted":
        conflict_row = db.execute(text(
            "SELECT c.id AS conflict_id, "
            "CASE WHEN c.memory_id_a = :id THEN c.memory_id_b ELSE c.memory_id_a END AS other_memory_id "
            "FROM agent_memory_conflicts c "
            "WHERE (c.memory_id_a = :id OR c.memory_id_b = :id) AND c.status = 'open'"
        ), {"id": memory_id}).mappings().one_or_none()
        if conflict_row is not None:
            other_text = db.execute(text(
                "SELECT display_text FROM agent_memories WHERE id = :id"
            ), {"id": conflict_row["other_memory_id"]}).scalar_one()
            result["conflict"] = {
                "conflict_id": conflict_row["conflict_id"],
                "other_memory_id": conflict_row["other_memory_id"],
                "other_display_text": other_text,
            }
    return result


import uuid


def _new_id() -> str:
    return str(uuid.uuid4())


def confirm_memory(db: Session, *, user_id: str, memory_id: str, consent: bool) -> dict:
    if not consent:
        raise MemoryConsentRequiredError("MEMORY_CONSENT_REQUIRED")
    row = db.execute(text(
        "SELECT security_domain_id, agent_id, status FROM agent_memories "
        "WHERE id = :id AND user_id = :u"
    ), {"id": memory_id, "u": user_id}).mappings().one_or_none()
    if row is None:
        raise MemoryConsentRequiredError("MEMORY_CONSENT_REQUIRED")
    if row["status"] == "conflicted":
        raise MemoryConflictError("MEMORY_CONFLICT")

    from app.services.memory.consent import grant_consent
    consent_id = grant_consent(db, security_domain_id=row["security_domain_id"],
                               agent_id=row["agent_id"], user_id=user_id,
                               consent_basis="explicit_confirmation", commit=False)
    db.execute(text(
        "UPDATE agent_memories SET status = 'active', updated_at = now() WHERE id = :id"
    ), {"id": memory_id})
    db.execute(text(
        "UPDATE agent_memory_revisions SET consent_id = :cid "
        "WHERE memory_id = :id AND superseded_at IS NULL"
    ), {"cid": consent_id, "id": memory_id})
    db.execute(text(
        "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
        "VALUES (:id, :mid, 'upsert', 'pending', now())"
    ), {"id": _new_id(), "mid": memory_id})
    db.commit()
    return get_memory(db, user_id=user_id, memory_id=memory_id)


def reject_memory(db: Session, *, user_id: str, memory_id: str) -> None:
    db.execute(text(
        "UPDATE agent_memories SET status = 'deleted', deleted_at = now(), updated_at = now() "
        "WHERE id = :id AND user_id = :u AND status != 'deleted'"
    ), {"id": memory_id, "u": user_id})
    db.commit()
