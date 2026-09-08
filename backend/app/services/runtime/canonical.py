"""Task 19: transport-independent canonicalization for Runtime results.

Every Runtime transport (REST, the Python SDK, MCP, the built-in reference
Agent) calls the exact same `RuntimeService` (Task 15), but each one wraps
the result in its own shape: REST/MCP hand back plain JSON-decoded dicts,
the SDK decodes into its own Pydantic models, and the reference Agent
returns the backend's own typed `InvestigationResult`/`ActionPlan` objects
untouched. None of that transport-specific shape is semantically
meaningful — a caller only cares about the decision, its evidence, and (for
an `ActionPlan`) the exact semantic fields the plan pins.

This module is the single place that reduces any of those four shapes to
one normalized, order-independent representation:

- `normalize_investigation` — the normalized decision every transport must
  agree on (Milestone 2/3 Scope Amendment's tiered parity: REST, SDK, MCP,
  and the reference Agent all match here).
- `canonical_plan_fields` / `compute_plan_hash` — the canonical byte
  serialization only REST and the SDK are held to byte-identical parity on;
  MCP and the reference Agent are not required to reproduce this exact
  digest, only the normalized decision content above.

HTTP/MCP envelopes, tracing/request IDs (`correlation_id`), generated
storage IDs (`id`), and other presentation-only fields are never part of
either normalized form — including them would make two independent,
otherwise-equivalent calls compare unequal for reasons that have nothing to
do with the semantic decision or plan being described.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Mapping, Sequence


def _get(source: Any, key: str, default: Any = None) -> Any:
    """Field access that works uniformly across a plain dict (REST/MCP), a
    Pydantic model (the SDK's or the backend's own schemas), or a dataclass
    (the backend's `ActionPlan`) — the one place this module has to know
    that those are different shapes at all."""
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _as_plain(value: Any) -> Any:
    """Reduce a Pydantic model, dataclass field, mapping, sequence, enum, or
    datetime-like value to a plain JSON-safe Python value, recursively —
    so `json.dumps` never has to know about any transport's model classes."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return _as_plain(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _as_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_plain(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _sorted_records(records: Any, id_fields: Sequence[str]) -> list[dict]:
    """Normalize a missing collection to `[]`, and reduce every record to a
    plain dict, sorted by its stable id field(s) — no transport promises
    evidence/rule-outcome ordering, so ordering must never affect the
    normalized or canonical form."""
    plain_records = [_as_plain(record) for record in (records or [])]

    def _sort_key(record: dict) -> tuple[str, ...]:
        return tuple(str(record.get(field, "")) for field in id_fields)

    return sorted(plain_records, key=_sort_key)


def normalize_investigation(result: Any) -> dict[str, Any]:
    """The normalized decision every Runtime transport must agree on:
    `decision`, `reason_code`, the snapshot/release pin, evidence, the rule
    outcomes, and the query result itself — never `correlation_id` (a
    tracing id) or any other transport envelope field.

    Accepts any of the four transports' native result shapes: a REST/MCP
    dict, an SDK or backend Pydantic model, or (for a denied SDK call) the
    SDK's own `RuntimeDeniedError`, which does not carry every field a full
    `InvestigationResult` does — missing fields normalize to `None`/`[]`
    exactly like an absent optional collection would.
    """
    raw_result = _get(result, "result")
    return {
        "decision": _as_plain(_get(result, "decision")),
        "reason_code": _as_plain(_get(result, "reason_code")),
        "semantic_snapshot_id": _get(result, "semantic_snapshot_id"),
        "ontology_release_id": _get(result, "ontology_release_id"),
        "evidence_citations": _sorted_records(
            _get(result, "evidence_citations", []), ("source_id", "content_hash"),
        ),
        "rule_outcome": _sorted_records(_get(result, "rule_outcome", []), ("rule_id",)),
        "result": _as_plain(raw_result) if raw_result is not None else None,
    }


# The semantic fields an `ActionPlan` pins: snapshot/release pins, verified
# principals, evidence/rules, binding identity/version, frozen typed
# parameters, the normalized target, before-image/version hashes,
# risk/policy, preconditions, expiry, and the idempotency key. Deliberately
# excluded: `id` (a generated storage id) and `plan_hash` itself (this
# module's own digest is an independent, externally-recomputable transport-
# parity check — it is NOT the persisted `plan_hash` and is not expected to
# equal it; the two intentionally cover different field sets and serve
# different purposes). Also excluded: `input_facts`, `predicted_diff`, and
# `impact_scope` (derived/presentation projections of the same pinned facts,
# not independent semantic inputs).
_PLAN_SCALAR_FIELDS = (
    "semantic_snapshot_id",
    "ontology_release_id",
    "agent_id",
    "user_id",
    "action_id",
    "managed_action_binding_id",
    "binding_version",
    "before_image_hash",
    "version_hash",
    "risk_classification",
    "expiry",
    "idempotency_key",
)


def canonical_plan_fields(plan: Any) -> bytes:
    """Serialize only `plan`'s semantic fields to canonical JSON bytes:
    sorted keys, compact separators, UTF-8, evidence/rule records sorted by
    their stable ids. Two calls describing the same proposal produce
    byte-identical output regardless of which transport built `plan` —
    REST's dict, the SDK's `ActionPlan` model, or the backend's own
    dataclass all read the same way through `_get`/`_as_plain`.
    """
    fields: dict[str, Any] = {name: _as_plain(_get(plan, name)) for name in _PLAN_SCALAR_FIELDS}
    fields["evidence_citations"] = _sorted_records(
        _get(plan, "evidence_citations", []), ("source_id", "content_hash"),
    )
    fields["rule_outcomes"] = _sorted_records(_get(plan, "rule_outcomes", []), ("rule_id",))
    fields["parameters"] = _as_plain(_get(plan, "parameters", {}))
    fields["target_key"] = _as_plain(_get(plan, "target_key", []))
    fields["policy_decision"] = _as_plain(_get(plan, "policy_decision", {}))
    fields["precondition_hashes"] = _as_plain(_get(plan, "precondition_hashes", []))
    return json.dumps(
        fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def compute_plan_hash(plan: Any) -> str:
    """The SHA-256 digest of `canonical_plan_fields(plan)` — the one plan
    hash REST and the SDK are required to produce byte-identically for an
    equivalent proposal (Milestone 2/3 Scope Amendment's tiered parity)."""
    return hashlib.sha256(canonical_plan_fields(plan)).hexdigest()


__all__ = [
    "normalize_investigation",
    "canonical_plan_fields",
    "compute_plan_hash",
]
