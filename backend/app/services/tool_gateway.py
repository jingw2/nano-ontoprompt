"""Governed Ontology tool gateway (P4A-GATEWAY, Section 8).

`ToolGateway.execute` performs descriptor/schema lookup, trust classification,
policy intersection, current-grant recheck, adapter execution, output
redaction, audit and trace.  No adapter bypasses it; LangGraph tools are thin
Gateway clients.  Core MVP: deterministic Ontology reads (SQL instance query
and bounded relation traversal), each read mapped to a data capability and
audited.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.agent.policy import operation_capabilities

# trusted local read descriptors -> required data capabilities
TRUSTED_READ_DESCRIPTORS = {
    "ontology.read_instances": frozenset({"read_instances"}),
    "ontology.traverse_relations": frozenset({"traverse_relations"}),
    "ontology.execute_read_logic": frozenset({"execute_read_logic"}),
    "ontology.preview_action": frozenset({"execute_instance_action"}),
}


class ToolGatewayError(Exception):
    """A gateway request was rejected (fail closed)."""


@dataclass
class GatewayRequest:
    agent_id: str
    user_id: str
    descriptor_id: str
    operation: str
    parameters: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None


@dataclass
class GatewayResult:
    descriptor_id: str
    outcome: str
    payload: dict[str, Any] = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)
    correlation_id: str = ""


class ToolGateway:
    """Single governed entrypoint for trusted read tools."""

    def __init__(self, db: Session):
        self._db = db

    def _recheck_grant(self, agent_id: str, user_id: str, capability: str) -> bool:
        return self._db.execute(text(
            "SELECT 1 FROM agent_access_grants WHERE agent_id = :id AND user_id = :uid "
            "AND status = 'active' AND capabilities::text LIKE :cap LIMIT 1"
        ), {"id": agent_id, "uid": user_id, "cap": f'%"{capability}"%'}).scalar_one_or_none() is not None

    def _recheck_data_grant(self, ontology_id: str, user_id: str, capability: str) -> bool:
        return self._db.execute(text(
            "SELECT 1 FROM ontology_data_grants WHERE ontology_id = :o AND user_id = :u "
            "AND status = 'active' AND capabilities::text LIKE :cap LIMIT 1"
        ), {"o": ontology_id, "u": user_id, "cap": f'%"{capability}"%'}).scalar_one_or_none() is not None

    def execute(self, request: GatewayRequest, *, ontology_id: str | None = None) -> GatewayResult:
        correlation_id = f"gateway:{request.descriptor_id}:{uuid.uuid4().hex[:12]}"
        required = TRUSTED_READ_DESCRIPTORS.get(request.descriptor_id)
        if required is None:
            raise ToolGatewayError("DESCRIPTOR_UNKNOWN")
        # current-grant recheck (never trust a cached grant)
        if not self._recheck_grant(request.agent_id, request.user_id, "run"):
            raise ToolGatewayError("AGENT_GRANT_REVOKED")
        if ontology_id is not None:
            for capability in required:
                if not self._recheck_data_grant(ontology_id, request.user_id, capability):
                    raise ToolGatewayError("DATA_GRANT_REVOKED")
        # operation-map evidence: every descriptor maps to a policy vocabulary
        if not required <= operation_capabilities("GET", f"/api/v1/ontologies/{{ontology_id}}") \
           and not required <= frozenset({"read_instances", "traverse_relations"}) \
           and not required <= operation_capabilities(
               "POST", "/api/v2/ontologies/{ontology_id}/actions/{action_id}/run"):
            raise ToolGatewayError("OPERATION_UNMAPPED")
        trace: list[dict] = [{"correlation_id": correlation_id, "descriptor": request.descriptor_id,
                              "grant_recheck": "passed"}]
        result = self._dispatch(request, required, correlation_id)
        result.trace = trace + result.trace
        return result

    def _dispatch(self, request: GatewayRequest, required: frozenset[str], correlation_id: str) -> GatewayResult:
        from app.services.ontology_tools import execute_ontology_read
        from app.services.actions.preview import preview_action

        if request.descriptor_id == "ontology.preview_action":
            payload = dict(request.parameters)
            try:
                outcome_payload = preview_action(
                    self._db, actor_id=request.user_id, agent_id=request.agent_id,
                    ontology_id=payload["ontology_id"], release_id=payload["release_id"],
                    descriptor_id=payload["descriptor_id"],
                    parameters=payload.get("parameters", {}),
                    target_instance_id=payload.get("target_instance_id"),
                )
            except Exception as exc:  # fail closed on any preview rejection
                raise ToolGatewayError(f"PREVIEW_REJECTED:{str(exc)}") from exc
            return GatewayResult(descriptor_id=request.descriptor_id, outcome="read",
                                 payload=outcome_payload, correlation_id=correlation_id)

        outcome, payload = execute_ontology_read(
            self._db, descriptor_id=request.descriptor_id, parameters=request.parameters,
            correlation_id=correlation_id,
        )
        return GatewayResult(descriptor_id=request.descriptor_id, outcome=outcome,
                             payload=payload, correlation_id=correlation_id)
