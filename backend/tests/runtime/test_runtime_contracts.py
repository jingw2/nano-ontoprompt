"""Task 14: transport-neutral runtime contracts.

`InvestigationResult` is the one typed vocabulary every Runtime transport
(REST, SDK, MCP, the built-in Agent) uses for a snapshot-scoped
investigation outcome. Its own model validator is the enforcement point for
the non-leakage invariant: a denial never carries a result, and an
authorized investigation always carries a collection (possibly empty).
"""
import pytest
from pydantic import ValidationError

from app.schemas.runtime import (
    EvidenceCitation,
    InvestigationRequest,
    InvestigationResult,
    ReasonCode,
    RuleOutcome,
)


def test_empty_authorized_result_is_allow():
    result = InvestigationResult(
        decision="ALLOW", reason_code="ALLOW",
        semantic_snapshot_id="snap-valid-001",
        ontology_release_id="release-valid-001",
        evidence_citations=[], rule_outcome=[],
        result=[], correlation_id="corr-001",
    )
    assert result.decision == "ALLOW"
    assert result.result == []


def test_authorized_result_can_carry_data():
    citation = EvidenceCitation(
        source_id="source-001", source_type="csv",
        locator="fixture://source-001", content_hash="a" * 64,
    )
    outcome = RuleOutcome(rule_id="rule-001", result="satisfied", reason_code="ALLOW")
    result = InvestigationResult(
        decision="ALLOW", reason_code="ALLOW",
        semantic_snapshot_id="snap-valid-001",
        ontology_release_id="release-valid-001",
        evidence_citations=[citation], rule_outcome=[outcome],
        result=[{"entity_id": "SUP001"}], correlation_id="corr-002",
    )
    assert result.decision == "ALLOW"
    assert result.result == [{"entity_id": "SUP001"}]
    assert result.evidence_citations[0].source_id == "source-001"
    assert result.rule_outcome[0].reason_code == ReasonCode.ALLOW


@pytest.mark.parametrize("reason_code", [
    "AGENT_CAPABILITY_DENIED", "USER_ENTITLEMENT_DENIED", "POLICY_DENIED", "SNAPSHOT_STALE",
])
def test_denial_cannot_carry_a_result(reason_code):
    """Protected-content non-leakage: a denial must set result to null —
    constructing one with populated rows is a validation error, not
    something a caller could accidentally serialize and leak."""
    with pytest.raises(ValidationError):
        InvestigationResult(
            decision="DENY", reason_code=reason_code,
            semantic_snapshot_id="snap-valid-001",
            ontology_release_id="release-valid-001",
            evidence_citations=[], rule_outcome=[],
            result=[{"entity_id": "SUP001"}], correlation_id="corr-003",
        )


def test_allow_decision_cannot_have_a_null_result():
    with pytest.raises(ValidationError):
        InvestigationResult(
            decision="ALLOW", reason_code="ALLOW",
            semantic_snapshot_id="snap-valid-001",
            ontology_release_id="release-valid-001",
            evidence_citations=[], rule_outcome=[],
            result=None, correlation_id="corr-004",
        )


def test_decision_and_reason_code_must_agree():
    with pytest.raises(ValidationError):
        InvestigationResult(
            decision="ALLOW", reason_code="POLICY_DENIED",
            semantic_snapshot_id="snap-valid-001",
            ontology_release_id="release-valid-001",
            evidence_citations=[], rule_outcome=[],
            result=[], correlation_id="corr-005",
        )


def test_investigation_request_carries_no_identity_fields():
    request = InvestigationRequest(
        semantic_snapshot_id="snap-valid-001",
        query="supplier SUP001",
        ontology_id="ontology-001",
        entity_type="Supplier",
        filters={},
        limit=20,
    )
    assert not hasattr(request, "agent_id")
    assert not hasattr(request, "user_id")
    assert request.limit == 20


def test_reason_code_vocabulary_is_closed_and_complete():
    expected = {
        "ALLOW", "MISSING_DELEGATION", "INVALID_DELEGATION", "AUDIENCE_DENIED",
        "SCOPE_DENIED", "EXPIRED_DELEGATION", "REVOKED_DELEGATION",
        "AGENT_INACTIVE", "USER_INACTIVE",
        "AGENT_CAPABILITY_DENIED", "USER_ENTITLEMENT_DENIED", "CROSS_SECURITY_DOMAIN",
        "SNAPSHOT_NOT_FOUND", "SNAPSHOT_NOT_GOVERNED", "SNAPSHOT_STALE",
        "SNAPSHOT_FRESHNESS_HITL", "POLICY_DENIED", "ACTION_NOT_ELIGIBLE",
        "PLAN_EXPIRED", "PRECONDITION_CONFLICT", "BINDING_DRIFT", "UNSUPPORTED_ACTION",
        "ROW_COUNT_MISMATCH", "UNKNOWN_EXECUTION_OUTCOME", "INVALID_PLAN_HASH",
    }
    assert {member.value for member in ReasonCode} == expected
