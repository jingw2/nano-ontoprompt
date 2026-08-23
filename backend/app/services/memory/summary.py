"""Rolling short-term memory summary (P6B-1, Section 11).

Regenerates at most once per sweep per session, only past the configured
unsummarized-message threshold. A regeneration attempt that fails
validation (missing required schema fields — "unsupported fields fail
grounding") leaves the prior summary untouched, per spec. Best-effort: the
caller (Task 6's Celery sweep) treats any exception here as skip-and-log,
never a Turn-blocking failure — this function itself does not swallow
errors, that's the sweep's responsibility so this function stays testable
on its own success/failure paths.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.agent.memory_settings import validate_memory_settings
from app.services.runtime.tokenizer import count_tokens

REQUIRED_SUMMARY_FIELDS = ("confirmed_facts", "decisions", "unresolved_questions", "source_ordinals")


def _call_summarizer(*, provider: str, api_key: str, api_base: str | None, model: str,
                     transcript: str) -> dict:
    """Real model call — isolated in its own function so tests can monkeypatch
    it without a network/credential dependency."""
    from app.services.llm_service import chat_completion
    prompt = (
        "Summarize this conversation transcript into exactly this JSON schema: "
        '{"confirmed_facts": [string], "decisions": [string], '
        '"unresolved_questions": [string], "source_ordinals": [int, int]}. '
        "Only include facts/decisions actually stated in the transcript. "
        "source_ordinals is [first_ordinal, last_ordinal] covered.\n\n" + transcript
    )
    response = chat_completion(provider, api_key, api_base, model,
                               [{"role": "user", "content": prompt}], timeout=60)
    return json.loads(response["content"])


def _grounded(candidate: dict) -> bool:
    return isinstance(candidate, dict) and all(field in candidate for field in REQUIRED_SUMMARY_FIELDS)


def maybe_regenerate_summary(db: Session, *, session_id: str) -> bool:
    row = db.execute(text(
        "SELECT s.agent_id, v.id AS version_id, v.memory_settings, "
        "v.default_model_config_version_id, v.default_model_name "
        "FROM agent_sessions s "
        "JOIN agents a ON a.id = s.agent_id "
        "JOIN agent_versions v ON v.id = a.active_version_id "
        "WHERE s.id = :sid"
    ), {"sid": session_id}).mappings().one_or_none()
    if row is None:
        return False
    settings = validate_memory_settings(row["memory_settings"] or {})
    if not settings["short_term_enabled"]:
        return False

    existing = db.execute(text(
        "SELECT covers_to_ordinal FROM agent_memory_summaries WHERE session_id = :sid"
    ), {"sid": session_id}).mappings().one_or_none()
    since_ordinal = existing["covers_to_ordinal"] if existing else -1

    unsummarized = db.execute(text(
        "SELECT ordinal, role, content FROM agent_messages "
        "WHERE session_id = :sid AND ordinal > :since ORDER BY ordinal"
    ), {"sid": session_id, "since": since_ordinal}).mappings().all()
    if len(unsummarized) < settings["summary_threshold"]:
        return False

    transcript = "\n".join(f"[{m['ordinal']}] {m['role']}: {m['content']}" for m in unsummarized)
    from app.services.model_callers.extraction import resolve_llm_caller_by_version
    caller = resolve_llm_caller_by_version(db, row["default_model_config_version_id"])
    candidate = _call_summarizer(
        provider=caller["provider"], api_key=caller["api_key"], api_base=caller["api_base"],
        model=caller["model"], transcript=transcript,
    )
    if not _grounded(candidate):
        return False

    summary_text = json.dumps({
        "confirmed_facts": candidate["confirmed_facts"],
        "decisions": candidate["decisions"],
        "unresolved_questions": candidate["unresolved_questions"],
    }, ensure_ascii=False)
    covers_from = unsummarized[0]["ordinal"] if since_ordinal < 0 else since_ordinal + 1
    covers_to = unsummarized[-1]["ordinal"]
    source_hash = hashlib.sha256(transcript.encode()).hexdigest()
    token_count = count_tokens(summary_text, row["default_model_name"])

    db.execute(text(
        "INSERT INTO agent_memory_summaries "
        "(id, session_id, summary_text, covers_from_ordinal, covers_to_ordinal, "
        "source_message_hash, summary_model_name, summary_token_count, updated_at) "
        "VALUES (:id, :sid, :text, :from_ord, :to_ord, :hash, :model, :tokens, now()) "
        "ON CONFLICT (session_id) DO UPDATE SET "
        "summary_text = EXCLUDED.summary_text, covers_from_ordinal = EXCLUDED.covers_from_ordinal, "
        "covers_to_ordinal = EXCLUDED.covers_to_ordinal, source_message_hash = EXCLUDED.source_message_hash, "
        "summary_model_name = EXCLUDED.summary_model_name, summary_token_count = EXCLUDED.summary_token_count, "
        "updated_at = now()"
    ), {"id": _new_id(), "sid": session_id, "text": summary_text, "from_ord": covers_from,
        "to_ord": covers_to, "hash": source_hash, "model": row["default_model_name"], "tokens": token_count})
    db.commit()
    return True


def _new_id() -> str:
    import uuid
    return str(uuid.uuid4())
