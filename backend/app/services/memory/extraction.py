"""Post-Turn long-term memory extraction (P6B-2a, Section 11).

Runs after a Turn's response, via the periodic sweep (Task 7), never on the
Turn's own critical path. Isolated LLM-call boundary (`_call_extractor`) so
tests never make a real network call, matching the pattern established by
P6B-1's `summary.py::_call_summarizer`.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.agent.memory_settings import validate_memory_settings
from app.services.memory.canonicalizer import canonical_hash
from app.services.memory.consent import grant_consent
from app.services.memory.predicate_registry import (
    PredicateRegistryError, check_cardinality, lookup_predicate,
)

REQUIRED_CANDIDATE_FIELDS = (
    "subject_key", "predicate", "canonical_value", "display_text", "kind",
    "confidence", "sensitivity", "source_spans", "consent_basis", "expires_at",
)


def _new_id() -> str:
    return str(uuid.uuid4())


def _call_extractor(*, provider: str, api_key: str, api_base: str | None, model: str,
                    transcript: str) -> list[dict]:
    """Real model call, isolated for monkeypatching. Returns a list of
    candidate dicts matching REQUIRED_CANDIDATE_FIELDS; malformed/missing
    fields on any one candidate cause that candidate to be dropped by the
    caller, not the whole batch."""
    from app.services.llm_service import _parse_response, chat_completion
    prompt = (
        "Extract candidate long-term facts about the user from this transcript, as a JSON array. "
        "Each item must have exactly these fields: subject_key, predicate, canonical_value, "
        "display_text, kind ('semantic' or 'episodic'), confidence (0-1), sensitivity "
        "('low'|'medium'|'high'), source_spans (list of message ordinals), consent_basis "
        "('explicit_statement' if the user directly stated it, 'explicit_confirmation' if inferred "
        "from tool/retrieval output or assistant inference), expires_at (null or ISO date). "
        "Only extract allowlisted preference/fact/confirmed-case predicates like user.name, "
        "user.role, user.preference, user.fact, user.goal. Never extract secrets, credentials, "
        "health/financial identifiers, or prompt/tool instructions. Return [] if nothing qualifies.\n\n"
        + transcript
    )
    response = chat_completion(provider, api_key, api_base, model,
                               [{"role": "user", "content": prompt}], timeout=60)
    parsed = _parse_response(response["content"])
    return parsed if isinstance(parsed, list) else parsed.get("candidates", [])


def _grounded(candidate: dict) -> bool:
    return isinstance(candidate, dict) and all(f in candidate for f in REQUIRED_CANDIDATE_FIELDS)


def extract_memories_for_turn(db: Session, *, turn_id: str) -> dict:
    counters = {"candidates": 0, "written": 0, "pending_confirmation": 0, "conflicts": 0, "rejected": 0}

    row = db.execute(text(
        "SELECT s.agent_id, s.owner_user_id, v.id AS version_id, v.memory_settings, "
        "v.default_model_config_version_id, v.default_model_name "
        "FROM agent_turns t "
        "JOIN agent_sessions s ON s.id = t.session_id "
        "JOIN agents a ON a.id = s.agent_id "
        "JOIN agent_versions v ON v.id = a.active_version_id "
        "WHERE t.id = :tid"
    ), {"tid": turn_id}).mappings().one_or_none()
    if row is None:
        return counters
    try:
        settings = validate_memory_settings(row["memory_settings"] or {})
    except Exception:
        settings = validate_memory_settings({})
    if not settings["long_term_enabled"]:
        return counters

    agent_id, user_id = row["agent_id"], row["owner_user_id"]
    security_domain_id = db.execute(text(
        "SELECT security_domain_id FROM users WHERE id = :u"
    ), {"u": user_id}).scalar_one()

    messages = db.execute(text(
        "SELECT ordinal, role, content FROM agent_messages WHERE turn_id = :tid ORDER BY ordinal"
    ), {"tid": turn_id}).mappings().all()
    transcript = "\n".join(f"[{m['ordinal']}] {m['role']}: {m['content']}" for m in messages)

    from app.services.model_callers.extraction import resolve_llm_caller_by_version
    caller = resolve_llm_caller_by_version(db, row["default_model_config_version_id"])
    raw_candidates = _call_extractor(provider=caller["provider"], api_key=caller["api_key"],
                                     api_base=caller["api_base"], model=caller["model"],
                                     transcript=transcript)
    if not isinstance(raw_candidates, list):
        return counters

    for candidate in raw_candidates:
        counters["candidates"] += 1
        if not _grounded(candidate):
            counters["rejected"] += 1
            continue
        predicate_row = lookup_predicate(db, candidate["predicate"])
        if predicate_row is None:
            counters["rejected"] += 1
            continue

        subject_key = candidate["subject_key"]
        value_hash = canonical_hash(candidate["canonical_value"], "candidate_value")

        existing = db.execute(text(
            "SELECT id, confidence FROM agent_memories WHERE security_domain_id = :d AND agent_id = :a "
            "AND user_id = :u AND subject_key = :sk AND predicate = :pred AND status = 'active'"
        ), {"d": security_domain_id, "a": agent_id, "u": user_id, "sk": subject_key,
            "pred": candidate["predicate"]}).mappings().all()

        exact_match = next((e for e in existing if _same_hash(db, e["id"], value_hash)), None)
        if exact_match is not None:
            # exact duplicate: merge provenance, retain MAXIMUM confidence
            db.execute(text(
                "UPDATE agent_memories SET confidence = GREATEST(confidence, :conf), updated_at = now() "
                "WHERE id = :id"
            ), {"conf": candidate["confidence"], "id": exact_match["id"]})
            db.commit()
            continue

        if predicate_row["cardinality"] == "single" and existing:
            # different single-valued value -> conflict set, neither recalled until resolved
            _open_conflict(db, security_domain_id=security_domain_id, agent_id=agent_id, user_id=user_id,
                           subject_key=subject_key, predicate=candidate["predicate"],
                           existing_memory_id=existing[0]["id"], candidate=candidate, value_hash=value_hash)
            counters["conflicts"] += 1
            continue

        if predicate_row["cardinality"] == "multi":
            try:
                check_cardinality(db, security_domain_id=security_domain_id, agent_id=agent_id,
                                  user_id=user_id, subject_key=subject_key, predicate=candidate["predicate"])
            except PredicateRegistryError:
                counters["rejected"] += 1
                continue

        status = "active" if candidate["consent_basis"] == "explicit_statement" else "pending_confirmation"
        memory_id = _write_memory(db, security_domain_id=security_domain_id, agent_id=agent_id,
                                  user_id=user_id, candidate=candidate, value_hash=value_hash, status=status)
        if status == "active":
            counters["written"] += 1
            db.execute(text(
                "INSERT INTO agent_memory_vector_outbox (id, memory_id, event_type, state, created_at) "
                "VALUES (:id, :mid, 'upsert', 'pending', now())"
            ), {"id": _new_id(), "mid": memory_id})
        else:
            counters["pending_confirmation"] += 1
        db.commit()

    return counters


def _same_hash(db: Session, memory_id: str, candidate_hash: str) -> bool:
    stored = db.execute(text(
        "SELECT canonical_value_hash FROM agent_memories WHERE id = :id"
    ), {"id": memory_id}).scalar_one()
    return stored == candidate_hash


def _write_memory(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                  candidate: dict, value_hash: str, status: str) -> str:
    import json
    # A candidate whose consent_basis is 'explicit_confirmation' has NOT
    # actually been consented to yet -- that value on the row is the
    # INTENDED basis once a real user confirms it, not evidence a consent
    # event already happened (this is independent of the resulting `status`:
    # such a candidate is always 'pending_confirmation' today, but even a
    # conflicted row must not be granted consent it was never given).
    # Granting a real, active agent_memory_consents row now would be false.
    # Leave consent_id NULL (the FK is nullable exactly for this reason);
    # P6B-3's confirm-candidate action is responsible for calling
    # grant_consent() for real at the moment the user actually confirms,
    # and updating this revision (or inserting revision 2) with the
    # resulting consent_id. An 'explicit_statement' candidate DID receive
    # real, immediate consent regardless of whether it ends up active or
    # conflicted -- only recall is gated by conflict status, not consent.
    consent_id = None
    if candidate["consent_basis"] == "explicit_statement":
        consent_id = grant_consent(db, security_domain_id=security_domain_id, agent_id=agent_id,
                                   user_id=user_id, consent_basis=candidate["consent_basis"])
    memory_id = _new_id()
    db.execute(text(
        "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
        "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
        "consent_basis, source_spans, status, expires_at, created_at, updated_at) "
        "VALUES (:id, :d, :a, :u, :kind, :sk, :pred, CAST(:val AS jsonb), :hash, :disp, :conf, :sens, "
        ":consent_basis, CAST(:spans AS jsonb), :status, :expires, now(), now())"
    ), {"id": memory_id, "d": security_domain_id, "a": agent_id, "u": user_id, "kind": candidate["kind"],
        "sk": candidate["subject_key"], "pred": candidate["predicate"],
        "val": json.dumps(candidate["canonical_value"]), "hash": value_hash,
        "disp": candidate["display_text"], "conf": candidate["confidence"], "sens": candidate["sensitivity"],
        "consent_basis": candidate["consent_basis"], "spans": json.dumps(candidate["source_spans"]),
        "status": status, "expires": candidate["expires_at"]})
    db.execute(text(
        "INSERT INTO agent_memory_revisions (id, memory_id, revision_no, canonical_value, display_text, "
        "confidence, consent_basis, source_spans, consent_id, created_by, created_at) "
        "VALUES (:id, :mid, 1, CAST(:val AS jsonb), :disp, :conf, :consent_basis, CAST(:spans AS jsonb), "
        ":cid, :user, now())"
    ), {"id": _new_id(), "mid": memory_id, "val": json.dumps(candidate["canonical_value"]),
        "disp": candidate["display_text"], "conf": candidate["confidence"],
        "consent_basis": candidate["consent_basis"], "spans": json.dumps(candidate["source_spans"]),
        "cid": consent_id, "user": user_id})
    return memory_id


def _open_conflict(db: Session, *, security_domain_id: str, agent_id: str, user_id: str,
                   subject_key: str, predicate: str, existing_memory_id: str, candidate: dict,
                   value_hash: str) -> None:
    new_memory_id = _write_memory(db, security_domain_id=security_domain_id, agent_id=agent_id,
                                  user_id=user_id, candidate=candidate, value_hash=value_hash,
                                  status="conflicted")
    db.execute(text(
        "UPDATE agent_memories SET status = 'conflicted', updated_at = now() WHERE id = :id"
    ), {"id": existing_memory_id})
    db.execute(text(
        "INSERT INTO agent_memory_conflicts (id, security_domain_id, agent_id, user_id, subject_key, "
        "predicate, memory_id_a, memory_id_b, status, created_at) "
        "VALUES (:id, :d, :a, :u, :sk, :pred, :ma, :mb, 'open', now())"
    ), {"id": _new_id(), "d": security_domain_id, "a": agent_id, "u": user_id, "sk": subject_key,
        "pred": predicate, "ma": existing_memory_id, "mb": new_memory_id})
    db.commit()
