"""Deterministic token counting for context-budget enforcement (P6B-1,
Section 11: "Token counts use the exact pinned model tokenizer/version").

Exact for OpenAI-family models via `tiktoken.encoding_for_model`. Every
other provider (Anthropic, openai-compatible custom endpoints) falls back
to `cl100k_base` — not byte-exact for non-OpenAI tokenizers, but far closer
than a character-count heuristic, and there is no vendored tokenizer for
every provider this codebase supports."""
from __future__ import annotations

import tiktoken

_FALLBACK_ENCODING = "cl100k_base"


def _encoding_for(model_name: str):
    try:
        return tiktoken.encoding_for_model(model_name)
    except KeyError:
        return tiktoken.get_encoding(_FALLBACK_ENCODING)


def count_tokens(text: str, model_name: str) -> int:
    if not text:
        return 0
    return len(_encoding_for(model_name).encode(text))
