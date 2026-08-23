"""Deterministic context-budget allocation (P6B-1, Section 11).

Allocation order, exactly as specified: reserve system/tool-schema and
response budgets first, then allocate remaining input tokens to pending
interrupt, current user message, application state, required retrieval
sources, optional retrieval sources (by configured order, dropped first
under pressure), rolling summary, recalled memories, and newest-to-oldest
message pairs. Required material that still doesn't fit fails closed
before any model call — never truncated, never silently dropped.
"""
from __future__ import annotations

import json

from app.services.runtime.tokenizer import count_tokens

DEFAULT_TOTAL_BUDGET_TOKENS = 24_000
RESPONSE_RESERVE_TOKENS = 1_024


class ContextBudgetExceeded(Exception):
    """Required context material exceeds the model's input budget (fail closed)."""


def assemble_bounded_messages(
    *, system_prompt: str, tool_schemas: list[dict], application_state: dict,
    retrieval_required: list[str], retrieval_optional: list[str],
    summary_text: str | None, recalled_memories: list[str],
    history_rows: list[dict], pending_interrupt: str | None, user_message: str,
    model_name: str, budgets: dict, total_budget_tokens: int = DEFAULT_TOTAL_BUDGET_TOKENS,
) -> list[dict]:
    tool_schema_text = json.dumps(tool_schemas, ensure_ascii=False)
    system_tokens = count_tokens(system_prompt, model_name) + count_tokens(tool_schema_text, model_name)
    remaining = total_budget_tokens - system_tokens - RESPONSE_RESERVE_TOKENS
    if remaining < 0:
        raise ContextBudgetExceeded("CONTEXT_BUDGET_EXCEEDED: system prompt and tool schema alone exceed budget")

    required_parts: list[str] = []
    if pending_interrupt:
        required_parts.append(pending_interrupt)
    required_parts.append(user_message)
    if application_state:
        required_parts.append(json.dumps(application_state, ensure_ascii=False))
    required_parts.extend(retrieval_required)
    required_tokens = sum(count_tokens(part, model_name) for part in required_parts)
    if required_tokens > remaining:
        raise ContextBudgetExceeded(
            f"CONTEXT_BUDGET_EXCEEDED: required material needs {required_tokens} tokens, "
            f"only {remaining} available")
    remaining -= required_tokens

    included_optional: list[str] = []
    for item in retrieval_optional:
        cost = count_tokens(item, model_name)
        if cost <= remaining:
            included_optional.append(item)
            remaining -= cost
        # lowest-priority optional items (later in configured order) are
        # dropped first — this loop is already in configured order, so once
        # an item doesn't fit we simply skip it and keep checking the rest
        # in case a smaller later item still does.

    included_summary = None
    if summary_text:
        summary_budget = min(budgets.get("summary_token_budget", 1200), remaining)
        cost = count_tokens(summary_text, model_name)
        if cost <= summary_budget:
            included_summary = summary_text
            remaining -= cost

    included_recall: list[str] = []
    recall_budget = min(budgets.get("recall_token_budget", 800), remaining)
    for item in recalled_memories[: budgets.get("recall_count", 8)]:
        cost = count_tokens(item, model_name)
        if cost <= recall_budget:
            included_recall.append(item)
            recall_budget -= cost
            remaining -= cost

    max_pairs = budgets.get("message_pairs", 12)
    trimmed_history = history_rows[-(max_pairs * 2):] if history_rows else []
    kept_history: list[dict] = []
    for message in reversed(trimmed_history):
        # +4: per-message role/formatting overhead (OpenAI chat-format convention)
        cost = count_tokens(message.get("content") or "", model_name) + 4
        if cost > remaining:
            break
        kept_history.append(message)
        remaining -= cost
    kept_history.reverse()

    context_blob = {
        "application_state": application_state,
        "retrieval_required": retrieval_required,
        "retrieval_optional": included_optional,
    }
    if included_summary:
        context_blob["conversation_summary"] = included_summary
    if included_recall:
        context_blob["recalled_memories"] = included_recall

    system = system_prompt + "\n\n## OntoPrompt context\n" + json.dumps(context_blob, ensure_ascii=False)
    messages: list[dict] = [{"role": "system", "content": system}]
    if pending_interrupt:
        messages.append({"role": "user", "content": pending_interrupt})
    for message in kept_history:
        role = message["role"] if message["role"] in ("user", "assistant") else "user"
        messages.append({"role": role, "content": message.get("content") or ""})
    if not kept_history:
        messages.append({"role": "user", "content": user_message})
    return messages
