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
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.agent.memory_settings import DEFAULTS, MemorySettingsError, validate_memory_settings
from app.services.runtime.tokenizer import count_tokens

logger = logging.getLogger(__name__)

REQUIRED_SUMMARY_FIELDS = ("confirmed_facts", "decisions", "unresolved_questions", "source_ordinals")


def _call_summarizer(*, provider: str, api_key: str, api_base: str | None, model: str,
                     transcript: str) -> dict:
    """Real model call — isolated in its own function so tests can monkeypatch
    it without a network/credential dependency."""
    from app.services.llm_service import _parse_response, chat_completion
    prompt = (
        "Summarize this conversation transcript into exactly this JSON schema: "
        '{"confirmed_facts": [string], "decisions": [string], '
        '"unresolved_questions": [string], "source_ordinals": [int, int]}. '
        "Only include facts/decisions actually stated in the transcript. "
        "source_ordinals is [first_ordinal, last_ordinal] covered.\n\n" + transcript
    )
    response = chat_completion(provider, api_key, api_base, model,
                               [{"role": "user", "content": prompt}], timeout=60)
    return _parse_response(response["content"])


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
    try:
        settings = validate_memory_settings(row["memory_settings"] or {})
    except MemorySettingsError as exc:
        # Read-path strictness buys nothing here either (see the matching
        # fallback in langgraph_runtime._build_messages_and_tools): an Agent
        # version saved before this validator existed must not permanently
        # break its sweep — fall back to defaults instead of raising.
        logger.warning(
            "invalid memory_settings for session_id=%s, falling back to defaults: %s",
            session_id, exc,
        )
        settings = dict(DEFAULTS)
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

    # Plain prose, not JSON: message_budget.py embeds this string as
    # context_blob["conversation_summary"] and JSON-encodes the surrounding
    # blob exactly once. A json.dumps(...) here would double-encode it (an
    # escaped-JSON-string-inside-JSON) — the same defect already fixed once
    # for the ontology/tools context in langgraph_runtime.py.
    lines = []
    if candidate["confirmed_facts"]:
        lines.append("Confirmed facts: " + "; ".join(candidate["confirmed_facts"]))
    if candidate["decisions"]:
        lines.append("Decisions: " + "; ".join(candidate["decisions"]))
    if candidate["unresolved_questions"]:
        lines.append("Unresolved questions: " + "; ".join(candidate["unresolved_questions"]))
    summary_text = "\n".join(lines) if lines else "(no notable facts in this segment)"
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
