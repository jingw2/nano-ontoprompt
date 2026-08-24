"""Closed predicate allowlist + multi-value cardinality cap (P6B-2a,
Section 11: "Predicate registry versions declare single or multi
cardinality; unknown predicates are rejected, and multi-valued defaults cap
at 10 active values")."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

MULTI_VALUE_CAP = 10


class PredicateRegistryError(Exception):
    """MEMORY_POLICY_REJECTED (unknown predicate) or MEMORY_CARDINALITY_EXCEEDED (multi-value cap)."""


def lookup_predicate(db: Session, predicate: str) -> dict | None:
    row = db.execute(text(
        "SELECT predicate, cardinality FROM agent_memory_predicate_registry WHERE predicate = :p"
    ), {"p": predicate}).mappings().one_or_none()
    return dict(row) if row else None


def check_cardinality(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                      subject_key: str, predicate: str) -> None:
    count = db.execute(text(
        "SELECT count(*) FROM agent_memories WHERE security_domain_id = :d AND agent_id = :a "
        "AND user_id = :u AND subject_key = :sk AND predicate = :pred AND status = 'active'"
    ), {"d": security_domain_id, "a": agent_id, "u": user_id, "sk": subject_key, "pred": predicate}).scalar_one()
    if count >= MULTI_VALUE_CAP:
        raise PredicateRegistryError(f"MEMORY_CARDINALITY_EXCEEDED: {predicate} at cap ({MULTI_VALUE_CAP})")
