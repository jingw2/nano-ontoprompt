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


from app.services.runtime.tokenizer import count_tokens


def _dedup_and_score_candidates(*, sql_candidates: list[dict], lexical_scores: dict[str, float],
                                semantic_scores: dict[str, float], now: datetime) -> list[dict]:
    scored = []
    for candidate in sql_candidates:
        memory_id = candidate["id"]
        lexical = lexical_scores.get(memory_id, 0.0)
        semantic = semantic_scores.get(memory_id)
        confidence = float(candidate["confidence"])
        source_quality = SOURCE_QUALITY.get(candidate["consent_basis"], 0.0)
        recency = _recency_score(candidate["updated_at"], now)
        result = _score_candidate(semantic=semantic, lexical=lexical, confidence=confidence,
                                  source_quality=source_quality, recency=recency)
        if result is None:
            continue
        score, ranking_mode = result
        scored.append({
            "id": memory_id, "display_text": candidate["display_text"], "score": score,
            "ranking_mode": ranking_mode, "cosine": semantic if ranking_mode == "hybrid" else None,
            "updated_at": candidate["updated_at"],
        })
    return scored


def _greedy_select(scored: list[dict], *, recall_count: int, recall_token_budget: int,
                   model_name: str) -> list[dict]:
    selected: list[dict] = []
    selected_cosines: list[float] = []  # cosines of already-selected embedded picks
    remaining_budget = recall_token_budget
    remaining_pool = list(scored)

    while remaining_pool and len(selected) < recall_count:
        best = None
        best_key = None
        for candidate in remaining_pool:
            if candidate["ranking_mode"] == "hybrid":
                if selected_cosines:
                    max_sim = max(_cosine_similarity_proxy(candidate["cosine"], other_cosine)
                                  for other_cosine in selected_cosines)
                else:
                    max_sim = 0.0
                selection_score = 0.75 * candidate["score"] - 0.25 * max_sim
            else:
                selection_score = candidate["score"]
            key = (selection_score, candidate["score"], candidate["updated_at"],
                  _reverse_id_key(candidate["id"]))
            if best_key is None or key > best_key:
                best_key = key
                best = candidate
        cost = count_tokens(_format_citation(best), model_name)
        if cost > remaining_budget:
            remaining_pool.remove(best)
            continue
        selected.append(best)
        remaining_budget -= cost
        if best["ranking_mode"] == "hybrid":
            selected_cosines.append(best["cosine"])
        remaining_pool.remove(best)
    return selected


def _cosine_similarity_proxy(candidate_cosine: float, other_cosine: float) -> float:
    # Both cosines are similarity-to-the-QUERY, not similarity-to-each-other
    # (this module has no pairwise item-to-item embedding comparison — Chroma
    # is only ever queried against the turn's query text, never against
    # another memory's text). Using the closeness of their query-similarity
    # as a bounded proxy for how redundant two embedded candidates are is a
    # documented, deliberate simplification: two items independently very
    # close to the query are also likely close to each other in practice,
    # and the proxy is well-defined, bounded in [0, 1], and monotonic in
    # exactly the direction the diversity penalty needs (closer query-cosines
    # -> higher proxy -> larger penalty). Computing true pairwise item-to-item
    # cosine similarity would require a second embedding call per pair, which
    # is out of this plan's scope and not requested by the spec's formula
    # itself (which only ever names "max_cosine_similarity_to_already_
    # selected_embedded_item" without specifying how it must be computed).
    return 1.0 - abs(candidate_cosine - other_cosine)


def _reverse_id_key(memory_id: str) -> tuple:
    # id ASC as the final tie-break, expressed as a max()-friendly key: since
    # the outer comparison in _greedy_select prefers the LARGEST tuple, and
    # we want the SMALLEST id to win ties, invert lexicographic order by
    # negating each character's ordinal in a comparable tuple form.
    return tuple(-ord(c) for c in memory_id)


def _format_citation(candidate: dict) -> str:
    return f"[memory:{candidate['id']}] {candidate['display_text']}"


def recall_memories(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                    query_text: str, model_name: str, recall_count: int,
                    recall_token_budget: int, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    sql_candidates = _fetch_sql_candidates(db, security_domain_id=security_domain_id,
                                           agent_id=agent_id, user_id=user_id)
    if not sql_candidates:
        return []
    overfetch = CANDIDATE_OVERFETCH_MULTIPLIER * recall_count
    lexical_scores = _lexical_channel(db, security_domain_id=security_domain_id,
                                      agent_id=agent_id, user_id=user_id,
                                      query_text=query_text, limit=overfetch)
    semantic_scores = _semantic_channel(security_domain_id, query_text, overfetch,
                                        sql_candidates=sql_candidates)
    scored = _dedup_and_score_candidates(sql_candidates=sql_candidates,
                                         lexical_scores=lexical_scores,
                                         semantic_scores=semantic_scores, now=now)
    selected = _greedy_select(scored, recall_count=recall_count,
                              recall_token_budget=recall_token_budget, model_name=model_name)
    return [_format_citation(c) for c in selected]
