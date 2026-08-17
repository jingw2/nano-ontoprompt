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
from urllib.parse import urlparse

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

# untrusted external descriptors -> required data capability. Distinct from
# TRUSTED_READ_DESCRIPTORS: dispatch wraps results as UntrustedArtifact and
# the recheck below additionally verifies the specific external-tool
# binding (not just the Agent's own run grant), since a binding can be
# removed from an Agent version independently of the Agent's overall grant.
EXTERNAL_TOOL_DESCRIPTORS = {
    "external.search": frozenset({"external_tool_call"}),
}


def _safe_external_url(raw_url: str) -> str:
    """Only http(s) URLs pass; anything else (javascript:, data:, garbage)
    collapses to the empty string rather than reaching the model."""
    parsed = urlparse(raw_url or "")
    if parsed.scheme in ("http", "https") and parsed.hostname:
        return raw_url
    return ""


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
        if request.descriptor_id in EXTERNAL_TOOL_DESCRIPTORS:
            return self._execute_external(request, correlation_id)
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

    def _execute_external(self, request: GatewayRequest, correlation_id: str) -> GatewayResult:
        if not self._recheck_grant(request.agent_id, request.user_id, "run"):
            raise ToolGatewayError("AGENT_GRANT_REVOKED")
        agent_version_id = request.parameters.get("agent_version_id")
        tool_connection_version_id = request.parameters.get("tool_connection_version_id")
        binding_alive = self._db.execute(text(
            "SELECT 1 FROM agent_external_tool_bindings "
            "WHERE agent_version_id = :av AND tool_connection_version_id = :tcv"
        ), {"av": agent_version_id, "tcv": tool_connection_version_id}).scalar_one_or_none()
        if binding_alive is None:
            raise ToolGatewayError("EXTERNAL_TOOL_BINDING_REVOKED")
        version = self._db.execute(text(
            "SELECT tcv.approval_status, tcv.endpoint, tcv.credential_reference, tp.kind "
            "FROM tool_connection_versions tcv "
            "JOIN tool_connections tc ON tc.id = tcv.connection_id "
            "JOIN tool_providers tp ON tp.id = tc.provider_id "
            "WHERE tcv.id = :id"
        ), {"id": tool_connection_version_id}).mappings().one_or_none()
        if version is None or version["approval_status"] != "approved":
            raise ToolGatewayError("EXTERNAL_TOOL_VERSION_NOT_APPROVED")
        if version["kind"] != "search":
            raise ToolGatewayError("EXTERNAL_TOOL_KIND_UNSUPPORTED")

        from app.services.tools.search import SearchError, web_search
        try:
            results = web_search(
                endpoint=version["endpoint"] or "", api_key=version["credential_reference"],
                query=str(request.parameters.get("query") or ""),
                result_limit=int(request.parameters.get("result_limit") or 5),
            )
        except SearchError as exc:
            raise ToolGatewayError(f"EXTERNAL_TOOL_FAILED:{exc}") from exc
        except Exception as exc:  # raw transport errors (httpx.TimeoutException etc.) escape web_search's taxonomy
            raise ToolGatewayError(f"EXTERNAL_TOOL_FAILED:{type(exc).__name__}") from exc
        from app.services.untrusted_artifact import safe_markdown
        payload = {"results": [
            {"title": safe_markdown(r["title"] if isinstance(r["title"], str) else ""),
             "url": _safe_external_url(r["url"] if isinstance(r["url"], str) else ""),
             "content": r["artifact"].sanitized_content,
             "source": r["artifact"].source, "sensitivity": r["artifact"].sensitivity}
            for r in results
        ]}
        return GatewayResult(descriptor_id=request.descriptor_id, outcome="untrusted_read",
                             payload=payload, correlation_id=correlation_id,
                             trace=[{"correlation_id": correlation_id, "descriptor": request.descriptor_id,
                                    "grant_recheck": "passed", "binding_recheck": "passed"}])

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
