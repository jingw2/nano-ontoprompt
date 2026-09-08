"""Memory canonicalizer version memory-c14n-v1 (P6B-2a, Section 11).

Deterministic normalization so two differently-worded-but-equal facts
produce the same dedup hash. Every rule in this module corresponds to one
sentence in the spec's canonicalizer paragraph — see the docstring on each
function for its exact source rule.
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal

CANONICALIZER_VERSION = "memory-c14n-v1"


class CanonicalizationError(Exception):
    """Value cannot be canonicalized (NaN/infinity/mixed-type set/unsupported object)."""


class CaseSensitive(str):
    """Wrap a string to skip case-folding — schema-declared case-sensitive values."""


class SetSemantics(list):
    """Wrap a list to declare set semantics — sorted, not order-preserved."""


def _normalize_number(value) -> str:
    if isinstance(value, bool):
        raise CanonicalizationError("booleans are not numbers")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CanonicalizationError("NaN/infinity are not canonicalizable")
        value = Decimal(str(value))
    if isinstance(value, Decimal):
        text = format(value.normalize(), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
    raise CanonicalizationError(f"unsupported numeric type {type(value)}")


def canonicalize(value):
    """Recursively canonicalize a JSON-compatible value per every rule in the
    module docstring (NFKC + whitespace-collapse + case-fold for strings,
    string-form for numbers, UTC RFC3339 for timestamps, sorted keys for
    objects, sorted values for SetSemantics, unchanged order for plain lists).

    Every rule — including number normalization — applies at every level of
    nesting, not just at the top, so that structurally-equal facts (e.g. an
    `int` 3 vs. a `float` 3.0 nested under the same key) canonicalize to the
    same value and therefore hash identically.

    Call this exactly once, on raw (not-yet-canonicalized) input.
    canonicalize() is NOT idempotent for CaseSensitive-wrapped values: the
    output is a plain str that no longer carries the case-sensitivity
    marker, so re-canonicalizing already-canonicalized output would silently
    casefold a value the first pass deliberately preserved.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, CaseSensitive):
        # NFKC + whitespace-collapse still apply; only case-folding is skipped.
        text = unicodedata.normalize("NFKC", str(value))
        return " ".join(text.split())
    if isinstance(value, str):
        text = unicodedata.normalize("NFKC", value)
        text = " ".join(text.split())  # collapses all Unicode whitespace, trims ends
        return text.casefold()
    if isinstance(value, (int, float, Decimal)):
        return _normalize_number(value)
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, SetSemantics):
        # Type-check BEFORE canonicalization for SetSemantics
        types = {type(v) for v in value}
        if len(types) > 1:
            raise CanonicalizationError("mixed-type sets are not canonicalizable")
        items = [canonicalize(v) for v in value]
        return sorted(items)
    if isinstance(value, list):
        return [canonicalize(v) for v in value]
    if isinstance(value, dict):
        return {k: canonicalize(value[k]) for k in sorted(value)}
    raise CanonicalizationError(f"unsupported type {type(value)}")


def canonical_hash(value, value_type: str) -> str:
    canonical = canonicalize(value)
    payload = json.dumps(
        {"canonicalizer_version": CANONICALIZER_VERSION, "value_type": value_type, "value": canonical},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
