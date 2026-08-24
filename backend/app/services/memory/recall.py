"""Long-term memory recall (P6B-2b, Section 11).

Recall first filters SQL candidates by namespace/status/TTL, gathers up to
4*recall_count candidates from each of the lexical and vector channels,
scores each surviving candidate by whichever formula its evidence supports,
deduplicates across channels, and greedily selects a diverse, token-budget-
bounded set. See the plan's Global Constraints for the exact formulas.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

CANDIDATE_OVERFETCH_MULTIPLIER = 4


def _fetch_sql_candidates(db: Session, *, security_domain_id: str, agent_id: str,
                          user_id: str) -> list[dict]:
    rows = db.execute(text(
        "SELECT id, subject_key, predicate, canonical_value_hash, display_text, confidence, "
        "consent_basis, embedding_model_version, updated_at "
        "FROM agent_memories "
        "WHERE security_domain_id = :d AND agent_id = :a AND user_id = :u "
        "AND status = 'active' AND (expires_at IS NULL OR expires_at > now())"
    ), {"d": security_domain_id, "a": agent_id, "u": user_id}).mappings().all()
    return [dict(r) for r in rows]


def _lexical_channel(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                     query_text: str, limit: int) -> dict[str, float]:
    rows = db.execute(text(
        "SELECT m.id, ts_rank_cd(m.search_vector, plainto_tsquery('simple', :q)) AS rank "
        "FROM agent_memories m "
        "WHERE m.security_domain_id = :d AND m.agent_id = :a AND m.user_id = :u "
        "AND m.status = 'active' AND (m.expires_at IS NULL OR m.expires_at > now()) "
        "AND m.search_vector @@ plainto_tsquery('simple', :q) "
        "ORDER BY rank DESC LIMIT :limit"
    ), {"d": security_domain_id, "a": agent_id, "u": user_id, "q": query_text,
        "limit": limit}).mappings().all()
    if not rows:
        return {}
    ranks = {r["id"]: float(r["rank"]) for r in rows}
    positive = [v for v in ranks.values() if v > 0]
    if not positive:
        return {}
    min_rank, max_rank = min(positive), max(positive)
    if min_rank == max_rank:
        return {mid: 1.0 for mid, v in ranks.items() if v > 0}
    return {mid: (v - min_rank) / (max_rank - min_rank) for mid, v in ranks.items() if v > 0}
