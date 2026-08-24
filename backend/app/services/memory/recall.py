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


import math
from datetime import datetime, timezone

SOURCE_QUALITY = {
    "explicit_user_correction": 1.00,       # unreachable with today's schema (documented)
    "explicit_statement": 0.95,
    "explicit_confirmation": 0.90,
    "policy_approved_tool_result": 0.80,    # unreachable with today's schema (documented)
    "grounded_document_extraction": 0.75,   # unreachable with today's schema (documented)
}

SCORE_THRESHOLD = 0.60


def _semantic_channel(security_domain_id: str, query_text: str, limit: int, *,
                      sql_candidates: list[dict]) -> dict[str, float]:
    from app.services.memory import vector_store

    current_versions = {
        c["id"] for c in sql_candidates
        if c.get("embedding_model_version") == vector_store.MEMORY_EMBEDDING_MODEL_VERSION
    }
    if not current_versions:
        return {}
    hits = vector_store.query_similar(security_domain_id, query_text, limit)
    return {h["id"]: h["cosine"] for h in hits if h["id"] in current_versions}


def _recency_score(updated_at: datetime, now: datetime) -> float:
    age_days = (now - updated_at).total_seconds() / 86400.0
    return math.exp(-age_days / 30.0)


def _score_candidate(*, semantic: float | None, lexical: float, confidence: float,
                     source_quality: float, recency: float) -> tuple[float, str] | None:
    confidence = max(0.0, min(1.0, confidence))
    if semantic is not None:
        semantic_mapped = (semantic + 1.0) / 2.0
        score = (0.50 * semantic_mapped + 0.20 * lexical + 0.15 * confidence
                 + 0.10 * recency + 0.05 * source_quality)
        mode = "hybrid"
    else:
        if lexical <= 0.0:
            return None
        score = (0.40 * lexical + 0.30 * confidence + 0.20 * recency + 0.10 * source_quality)
        mode = "lexical_only"
    if score < SCORE_THRESHOLD:
        return None
    return score, mode
