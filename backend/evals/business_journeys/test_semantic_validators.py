"""Tests for validate_semantic_minimum: exact citations, case-insensitive
names, numeric predicates, and rejection of a generic non-empty answer."""
from __future__ import annotations

from evals.business_journeys.contracts import JourneySemanticMinimum
from evals.business_journeys.semantic_validators import validate_semantic_minimum

MINIMUM = JourneySemanticMinimum(
    entities=("Supplier", "PurchaseOrder"),
    relations=("SUPPLIES",),
    rules=("inventory_below_safety_stock",),
    actions=("risk_label",),
    keywords=("supplier", "safety stock"),
    source_citation_ids=("SRC-001",),
    numeric_predicates={
        "inventory_below_threshold": {"path": "numeric_facts.inventory_count", "op": "lt", "value": 50}
    },
    low_risk_action="risk_label",
    high_risk_action="purchase_order_price_update",
)


def _full_response(**overrides):
    base = {
        "entities": ["supplier", "PURCHASEORDER"],
        "relations": ["supplies"],
        "rules": ["inventory_below_safety_stock"],
        "actions": ["risk_label"],
        "citations": ["SRC-001"],
        "answer": "The supplier is below safety stock and needs a purchase order.",
        "numeric_facts": {"inventory_count": 12},
    }
    base.update(overrides)
    return base


def test_full_response_passes_case_insensitively():
    result = validate_semantic_minimum(_full_response(), MINIMUM)
    assert result.passed is True
    assert result.reason_codes == ()


def test_missing_entity_fails():
    result = validate_semantic_minimum(_full_response(entities=["supplier"]), MINIMUM)
    assert result.passed is False
    assert "PurchaseOrder" in result.missing_entities
    assert "MISSING_ENTITIES" in result.reason_codes


def test_generic_non_empty_answer_missing_keywords_fails():
    result = validate_semantic_minimum(
        _full_response(answer="Everything looks fine, no issues found."), MINIMUM
    )
    assert result.passed is False
    assert "MISSING_KEYWORDS" in result.reason_codes


def test_empty_answer_fails():
    result = validate_semantic_minimum(_full_response(answer=""), MINIMUM)
    assert result.passed is False
    assert "EMPTY_ANSWER" in result.reason_codes


def test_citation_ids_are_exact_not_case_insensitive():
    result = validate_semantic_minimum(_full_response(citations=["src-001"]), MINIMUM)
    assert result.passed is False
    assert "SRC-001" in result.missing_citations
    assert "MISSING_CITATIONS" in result.reason_codes


def test_numeric_predicate_failure_is_reported():
    result = validate_semantic_minimum(
        _full_response(numeric_facts={"inventory_count": 500}), MINIMUM
    )
    assert result.passed is False
    assert "inventory_below_threshold" in result.numeric_predicate_failures
    assert "NUMERIC_PREDICATE_FAILED" in result.reason_codes


def test_missing_numeric_fact_counts_as_failure():
    result = validate_semantic_minimum(_full_response(numeric_facts={}), MINIMUM)
    assert result.passed is False
    assert "inventory_below_threshold" in result.numeric_predicate_failures
