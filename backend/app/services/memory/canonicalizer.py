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


def _canonicalize_internal(value, in_container=False):
    """Internal canonicalization helper.

    When in_container=True, numbers are NOT converted to strings;
    only bare top-level numbers are stringified.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, CaseSensitive):
        return str(value)
    if isinstance(value, str):
        text = unicodedata.normalize("NFKC", value)
        text = " ".join(text.split())  # collapses all Unicode whitespace, trims ends
        return text.casefold()
    if isinstance(value, (int, float, Decimal)):
        if in_container:
            # Inside containers, preserve the number type but validate it
            if isinstance(value, float):
                if math.isnan(value) or math.isinf(value):
                    raise CanonicalizationError("NaN/infinity are not canonicalizable")
            return value
        else:
            # Top-level numbers are canonicalized to strings
            return _normalize_number(value)
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, SetSemantics):
        # Type-check BEFORE canonicalization for SetSemantics
        types = {type(v) for v in value}
        if len(types) > 1:
            raise CanonicalizationError("mixed-type sets are not canonicalizable")
        items = [_canonicalize_internal(v, in_container=True) for v in value]
        return sorted(items)
    if isinstance(value, list):
        return [_canonicalize_internal(v, in_container=True) for v in value]
    if isinstance(value, dict):
        return {k: _canonicalize_internal(value[k], in_container=True) for k in sorted(value)}
    raise CanonicalizationError(f"unsupported type {type(value)}")


def canonicalize(value):
    return _canonicalize_internal(value, in_container=False)


def canonical_hash(value, value_type: str) -> str:
    canonical = canonicalize(value)
    payload = json.dumps(
        {"canonicalizer_version": CANONICALIZER_VERSION, "value_type": value_type, "value": canonical},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
