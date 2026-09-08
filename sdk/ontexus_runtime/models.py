"""Task 17: typed request/response models for the Runtime SDK.

These mirror the transport-neutral contracts `backend/app/schemas/runtime.py`
(Task 14) and the wire shapes `backend/app/routers/v2/runtime.py` (Task 16)
serializes — same field names, same shapes — so the SDK decodes a Runtime
response faithfully rather than reinterpreting it. This module has no
dependency on backend code; it is a standalone, structurally identical
vocabulary for the SDK's own transport.

`extra="allow"` on response models means a field the server adds later is
kept on the decoded object instead of being silently dropped, preserving
byte-identical parity with what the server actually returned.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_id: str
    source_type: str
    locator: str
    content_hash: str


class RuleOutcome(BaseModel):
    model_config = ConfigDict(extra="allow")

    rule_id: str
    result: str
    reason_code: str


class InvestigationRequest(BaseModel):
    """The outgoing shape for `POST /api/v2/runtime/investigate`. Carries no
    identity fields — the caller's identity is always the delegated
    credential the transport injects, never a field here."""

    model_config = ConfigDict(extra="forbid")

    semantic_snapshot_id: str
    ontology_id: str
    query: str | None = None
    entity_type: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = 20


class InvestigationResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    decision: str
    reason_code: str
    semantic_snapshot_id: str
    ontology_release_id: str
    evidence_citations: list[EvidenceCitation] = Field(default_factory=list)
    rule_outcome: list[RuleOutcome] = Field(default_factory=list)
    result: list[Any] | None = None
    correlation_id: str


class ActionPlanRequest(BaseModel):
    """The outgoing shape for `POST /api/v2/runtime/action-plans`."""

    model_config = ConfigDict(extra="forbid")

    semantic_snapshot_id: str
    action_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    target_selector: dict[str, Any] | None = None
    idempotency_key: str | None = None


class ActionPlan(BaseModel):
    """A decoded `ActionPlan` — the same fields
    `app.routers.v2.runtime._serialize_action_plan` puts on the wire."""

    model_config = ConfigDict(extra="allow")

    id: str
    semantic_snapshot_id: str
    ontology_release_id: str
    agent_id: str
    user_id: str
    action_id: str
    input_facts: dict[str, Any] = Field(default_factory=dict)
    evidence_citations: list[EvidenceCitation] = Field(default_factory=list)
    rule_outcomes: list[RuleOutcome] = Field(default_factory=list)
    managed_action_binding_id: str | None = None
    binding_version: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    target_key: list[Any] = Field(default_factory=list)
    before_image_hash: str
    version_hash: str
    predicted_diff: dict[str, Any] = Field(default_factory=dict)
    impact_scope: dict[str, Any] = Field(default_factory=dict)
    risk_classification: str
    policy_decision: dict[str, Any] = Field(default_factory=dict)
    precondition_hashes: list[str] = Field(default_factory=list)
    expiry: str
    idempotency_key: str
    plan_hash: str


class ExecutionStatus(BaseModel):
    """A decoded `GET /api/v2/runtime/execution-status/{plan_id}` response."""

    model_config = ConfigDict(extra="allow")

    plan_id: str
    status: str
    correlation_id: str | None = None


__all__ = [
    "EvidenceCitation",
    "RuleOutcome",
    "InvestigationRequest",
    "InvestigationResult",
    "ActionPlanRequest",
    "ActionPlan",
    "ExecutionStatus",
]
