"""Typed validation for AgentVersion.memory_settings (P6B-1, Section 11).

Every key here is defined by the spec's deterministic-budget paragraph.
`long_term_enabled`, `recall_token_budget`, and `recall_count` are validated
and defaulted now but stay functionally inert until P6B-2 lands the
extraction/recall pipeline that actually reads them — this keeps P6B-2
additive rather than a breaking schema change to what P6B-1 already
persists.
"""
from __future__ import annotations


class MemorySettingsError(Exception):
    """Rejected memory_settings payload (MEMORY_POLICY_REJECTED)."""


DEFAULTS = {
    "short_term_enabled": True,
    "long_term_enabled": False,
    "message_pairs": 12,
    "summary_threshold": 24,
    "summary_token_budget": 1200,
    "recall_token_budget": 800,
    "recall_count": 8,
}

# (min, max) inclusive ranges for the integer keys; the two bool keys have no range.
RANGES = {
    "message_pairs": (2, 20),
    "summary_threshold": (8, 40),
    "summary_token_budget": (256, 2048),
    "recall_token_budget": (128, 1200),
    "recall_count": (1, 12),
}


def validate_memory_settings(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise MemorySettingsError("MEMORY_POLICY_REJECTED")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise MemorySettingsError(f"MEMORY_POLICY_REJECTED: unknown keys {sorted(unknown)}")
    settings = dict(DEFAULTS)
    settings.update(raw)
    for key in ("short_term_enabled", "long_term_enabled"):
        if not isinstance(settings[key], bool):
            raise MemorySettingsError(f"MEMORY_POLICY_REJECTED: {key} must be a boolean")
    for key, (lo, hi) in RANGES.items():
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise MemorySettingsError(f"MEMORY_POLICY_REJECTED: {key} must be an integer")
        if not (lo <= value <= hi):
            raise MemorySettingsError(f"MEMORY_POLICY_REJECTED: {key} must be in [{lo}, {hi}]")
    return settings
