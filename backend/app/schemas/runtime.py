"""Transport-neutral runtime contracts (Task 14).

One typed request/result/error vocabulary shared by every Runtime transport
(REST, SDK, MCP, the built-in Agent) for snapshot-scoped investigation,
evidence, domain-rule outcomes, and policy decisions. `ReasonCode` is the
single closed vocabulary every Runtime denial (credential, policy,
snapshot-freshness, or action-plan) is expressed in; a caller can always
switch on `reason_code` without transport-specific translation.

`InvestigationResult` enforces its own non-leakage invariant structurally:
a denial (`decision != "ALLOW"`) never carries a `result`, and an
authorized investigation (`decision == "ALLOW"`) always carries a
collection — possibly empty, but never `None`. An authorized query that
matches nothing is therefore never confused with a denial.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReasonCode(str, Enum):
    ALLOW = "ALLOW"
    MISSING_DELEGATION = "MISSING_DELEGATION"
    INVALID_DELEGATION = "INVALID_DELEGATION"
    AUDIENCE_DENIED = "AUDIENCE_DENIED"
    SCOPE_DENIED = "SCOPE_DENIED"
    EXPIRED_DELEGATION = "EXPIRED_DELEGATION"
    REVOKED_DELEGATION = "REVOKED_DELEGATION"
    AGENT_INACTIVE = "AGENT_INACTIVE"
    USER_INACTIVE = "USER_INACTIVE"
    AGENT_CAPABILITY_DENIED = "AGENT_CAPABILITY_DENIED"
    USER_ENTITLEMENT_DENIED = "USER_ENTITLEMENT_DENIED"
    CROSS_SECURITY_DOMAIN = "CROSS_SECURITY_DOMAIN"
    SNAPSHOT_NOT_FOUND = "SNAPSHOT_NOT_FOUND"
    SNAPSHOT_NOT_GOVERNED = "SNAPSHOT_NOT_GOVERNED"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    SNAPSHOT_FRESHNESS_HITL = "SNAPSHOT_FRESHNESS_HITL"
    POLICY_DENIED = "POLICY_DENIED"
    ACTION_NOT_ELIGIBLE = "ACTION_NOT_ELIGIBLE"
    PLAN_EXPIRED = "PLAN_EXPIRED"
    PRECONDITION_CONFLICT = "PRECONDITION_CONFLICT"
    BINDING_DRIFT = "BINDING_DRIFT"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    ROW_COUNT_MISMATCH = "ROW_COUNT_MISMATCH"
    UNKNOWN_EXECUTION_OUTCOME = "UNKNOWN_EXECUTION_OUTCOME"
    INVALID_PLAN_HASH = "INVALID_PLAN_HASH"


class EvidenceCitation(BaseModel):
    """A hashable pointer to one governed source, never the source content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    source_type: str
    locator: str
    content_hash: str


class RuleOutcome(BaseModel):
    """One domain rule's evaluated outcome against the investigated data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    result: str
    reason_code: ReasonCode


class InvestigationRequest(BaseModel):
    """A snapshot-pinned, policy-checked read request. Carries no authority
    of its own — the caller's identity is always the verified RuntimeContext,
    never a field on this request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    semantic_snapshot_id: str
    query: str | None = None
    ontology_id: str
    entity_type: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = 20


class InvestigationResult(BaseModel):
    """`decision` is the coarse ALLOW/DENY outcome; `reason_code` is the
    specific cause (== ALLOW exactly when `decision == "ALLOW"`). Denials
    set `result` to null; authorized no-match queries set `result` to an
    empty collection with `decision` ALLOW — a denial is never mistaken for
    an authorized empty result, or vice versa."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Literal["ALLOW", "DENY"]
    reason_code: ReasonCode
    semantic_snapshot_id: str
    ontology_release_id: str
    evidence_citations: list[EvidenceCitation] = Field(default_factory=list)
    rule_outcome: list[RuleOutcome] = Field(default_factory=list)
    result: list[Any] | None = None
    correlation_id: str
    # Task 20: freshness metadata (never protected row content) — safe to
    # carry on both ALLOW and DENY, since it explains staleness rather than
    # leaking query results.
    freshness_state: str | None = None
    freshness_lag_seconds: int | None = None
    source_cursor: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_result_matches_decision(self) -> "InvestigationResult":
        is_allow = self.decision == "ALLOW"
        if is_allow != (self.reason_code == ReasonCode.ALLOW):
            raise ValueError("decision and reason_code must agree on ALLOW")
        if is_allow and self.result is None:
            raise ValueError("an ALLOW decision must carry a result collection, even if empty")
        if not is_allow and self.result is not None:
            raise ValueError("a denial must not carry a result — protected rows never leak on deny")
        return self
