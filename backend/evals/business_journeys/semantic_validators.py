"""Semantic-minimum validation against a journey's required contract.

Names (entities/relations/rules/actions/keywords) are compared
case-insensitively; citation IDs and numeric predicates are compared
exactly. A generic, non-empty answer that is missing required entities or
keywords still fails.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from .contracts import JourneySemanticMinimum, SemanticValidation

_NUMERIC_OPS = {
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}


def _canon(value: object) -> str:
    return str(value).strip().casefold()


def _collect_strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return (str(value),)


def _lookup_path(payload: Mapping[str, object], dotted_path: str) -> object:
    node: object = payload
    for segment in dotted_path.split("."):
        if not isinstance(node, Mapping) or segment not in node:
            return None
        node = node[segment]
    return node


def _check_numeric_predicates(
    response: Mapping[str, object], numeric_predicates: Mapping[str, Mapping[str, object]]
) -> tuple[str, ...]:
    failures: list[str] = []
    for name, predicate in numeric_predicates.items():
        path = str(predicate.get("path", name))
        op = str(predicate.get("op", "eq"))
        expected = predicate.get("value")
        observed = _lookup_path(response, path)
        comparator = _NUMERIC_OPS.get(op)
        if comparator is None or observed is None or not isinstance(observed, (int, float)):
            failures.append(name)
            continue
        try:
            if not comparator(observed, expected):
                failures.append(name)
        except TypeError:
            failures.append(name)
    return tuple(failures)


def validate_semantic_minimum(response: Mapping[str, object], minimum: JourneySemanticMinimum) -> SemanticValidation:
    """Check ``response`` against every required part of ``minimum``.

    ``response`` is expected to expose ``entities``, ``relations``, ``rules``,
    ``actions``, ``citations``, and ``answer`` top-level fields (plus
    whatever nested numeric facts ``minimum.numeric_predicates`` addresses by
    dotted path); anything missing counts as absent, not an error.
    """
    canon_entities = {_canon(v) for v in _collect_strings(response.get("entities"))}
    canon_relations = {_canon(v) for v in _collect_strings(response.get("relations"))}
    canon_rules = {_canon(v) for v in _collect_strings(response.get("rules"))}
    canon_actions = {_canon(v) for v in _collect_strings(response.get("actions"))}
    citation_ids = set(_collect_strings(response.get("citations")))
    answer_text = str(response.get("answer") or "")
    canon_answer = _canon(answer_text)

    missing_entities = tuple(e for e in minimum.entities if _canon(e) not in canon_entities)
    missing_relations = tuple(r for r in minimum.relations if _canon(r) not in canon_relations)
    missing_rules = tuple(r for r in minimum.rules if _canon(r) not in canon_rules)
    missing_actions = tuple(a for a in minimum.actions if _canon(a) not in canon_actions)
    missing_citations = tuple(c for c in minimum.source_citation_ids if c not in citation_ids)
    missing_keywords = tuple(k for k in minimum.keywords if _canon(k) not in canon_answer)
    numeric_predicate_failures = _check_numeric_predicates(response, minimum.numeric_predicates)

    reason_codes: list[str] = []
    if not answer_text.strip():
        reason_codes.append("EMPTY_ANSWER")
    if missing_entities:
        reason_codes.append("MISSING_ENTITIES")
    if missing_relations:
        reason_codes.append("MISSING_RELATIONS")
    if missing_rules:
        reason_codes.append("MISSING_RULES")
    if missing_actions:
        reason_codes.append("MISSING_ACTIONS")
    if missing_citations:
        reason_codes.append("MISSING_CITATIONS")
    if missing_keywords:
        reason_codes.append("MISSING_KEYWORDS")
    if numeric_predicate_failures:
        reason_codes.append("NUMERIC_PREDICATE_FAILED")

    return SemanticValidation(
        passed=not reason_codes,
        missing_entities=missing_entities,
        missing_relations=missing_relations,
        missing_rules=missing_rules,
        missing_actions=missing_actions,
        missing_citations=missing_citations,
        missing_keywords=missing_keywords,
        numeric_predicate_failures=numeric_predicate_failures,
        reason_codes=tuple(reason_codes),
    )
