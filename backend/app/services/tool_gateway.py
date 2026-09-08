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
    "external.playwright": frozenset({"external_tool_call"}),
    "external.skill": frozenset({"external_tool_call"}),
    "external.mcp": frozenset({"external_tool_call"}),
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
        if request.descriptor_id == "external.skill":
            return self._execute_skill(request, correlation_id)
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
            "SELECT tcv.approval_status, tcv.endpoint, tcv.credential_reference, tcv.allowlists, tp.kind "
            "FROM tool_connection_versions tcv "
            "JOIN tool_connections tc ON tc.id = tcv.connection_id "
            "JOIN tool_providers tp ON tp.id = tc.provider_id "
            "WHERE tcv.id = :id"
        ), {"id": tool_connection_version_id}).mappings().one_or_none()
        if version is None or version["approval_status"] != "approved":
            raise ToolGatewayError("EXTERNAL_TOOL_VERSION_NOT_APPROVED")
        if version["kind"] == "playwright":
            return self._execute_playwright(request, version, correlation_id)
        if version["kind"] == "external_mcp":
            return self._execute_mcp(request, correlation_id)
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

    def _execute_playwright(self, request: GatewayRequest, version, correlation_id: str) -> GatewayResult:
        from app.services.tools.playwright import PlaywrightError, browse_page
        from app.services.untrusted_artifact import safe_markdown
        allowlists = version["allowlists"] or {}
        domains = [str(d) for d in (allowlists.get("domains") or [])]
        try:
            result = browse_page(
                url=str(request.parameters.get("url") or ""),
                allowed_domains=domains,
                timeout_seconds=float(request.parameters.get("timeout_seconds") or 20),
                max_bytes=200_000,
            )
        except PlaywrightError as exc:
            raise ToolGatewayError(f"EXTERNAL_TOOL_FAILED:{exc}") from exc
        except Exception as exc:  # raw transport errors escape the adapter's taxonomy
            raise ToolGatewayError(f"EXTERNAL_TOOL_FAILED:{type(exc).__name__}") from exc
        payload = {"results": [
            {"title": safe_markdown(result["title"] if isinstance(result["title"], str) else ""),
             "url": _safe_external_url(result["final_url"] if isinstance(result["final_url"], str) else ""),
             "content": result["artifact"].sanitized_content,
             "source": result["artifact"].source, "sensitivity": result["artifact"].sensitivity}
        ]}
        return GatewayResult(descriptor_id=request.descriptor_id, outcome="untrusted_read",
                             payload=payload, correlation_id=correlation_id,
                             trace=[{"correlation_id": correlation_id, "descriptor": request.descriptor_id,
                                    "grant_recheck": "passed", "binding_recheck": "passed"}])

    def _execute_mcp(self, request: GatewayRequest, correlation_id: str) -> GatewayResult:
        from app.services.encryption_service import decrypt
        from app.services.tools.mcp_client import MCPClientError, call_tool, list_tools, tools_schema_hash
        from app.services.untrusted_artifact import safe_markdown

        version_id = request.parameters.get("tool_connection_version_id")
        row = self._db.execute(text(
            "SELECT tcv.endpoint, tcv.allowlists, tcv.scopes FROM tool_connection_versions tcv "
            "WHERE tcv.id = :id"
        ), {"id": version_id}).mappings().one_or_none()
        if row is None:
            raise ToolGatewayError("EXTERNAL_TOOL_VERSION_NOT_APPROVED")
        schema_row = self._db.execute(text(
            "SELECT tool_schema_hash, quarantined FROM mcp_connection_schemas "
            "WHERE connection_version_id = :id"
        ), {"id": version_id}).mappings().one_or_none()
        if schema_row is None:
            raise ToolGatewayError("MCP_SCHEMA_UNPINNED")
        if schema_row["quarantined"]:
            # already flagged — fail fast without another network call
            raise ToolGatewayError("TOOL_SCHEMA_QUARANTINED")
        token_row = self._db.execute(text(
            "SELECT encrypted_access_token, scope, expires_at FROM mcp_oauth_tokens "
            "WHERE connection_version_id = :id"
        ), {"id": version_id}).mappings().one_or_none()
        if token_row is None:
            raise ToolGatewayError("MCP_TOKEN_MISSING")
        import datetime as _dt
        expires_at = token_row["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)
        if expires_at <= _dt.datetime.now(_dt.timezone.utc):
            raise ToolGatewayError("MCP_TOKEN_EXPIRED")
        declared_scopes = set(row["scopes"] or [])
        token_scopes = set(token_row["scope"] or [])
        # Symmetric equality, not subset: a token with FEWER scopes than
        # declared is under-privileged for the connection, but a token with
        # MORE scopes than declared is over-privileged and must be rejected
        # too — dispatch may only proceed on an exact match.
        if declared_scopes != token_scopes:
            raise ToolGatewayError("OAUTH_SCOPE_DENIED")
        # `audience` is recorded on both mcp_oauth_tokens and
        # tool_connection_versions but is not compared/enforced here — a
        # documented residual gap (see ssrf_guard.py's DNS-rebinding note
        # for the same "known gap, not silently assumed closed" pattern),
        # not an oversight. What tool_connection_versions.audience should
        # mean as a comparison target is a bigger design question deferred
        # to a future pass.
        domains = [str(d) for d in ((row["allowlists"] or {}).get("domains") or [])]
        access_token = decrypt(token_row["encrypted_access_token"])
        try:
            live_tools = list_tools(endpoint=row["endpoint"] or "", access_token=access_token,
                                    allowed_domains=domains)
        except MCPClientError as exc:
            raise ToolGatewayError(f"EXTERNAL_TOOL_FAILED:{exc}") from exc
        except Exception as exc:  # raw transport errors escape the adapter's taxonomy
            raise ToolGatewayError(f"EXTERNAL_TOOL_FAILED:{type(exc).__name__}") from exc
        if tools_schema_hash(live_tools) != schema_row["tool_schema_hash"]:
            self._db.execute(text(
                "UPDATE mcp_connection_schemas SET quarantined = true, quarantined_at = now(), "
                "quarantined_reason = 'SCHEMA_DRIFT_AT_DISPATCH' WHERE connection_version_id = :id"
            ), {"id": version_id})
            self._db.commit()
            raise ToolGatewayError("TOOL_SCHEMA_QUARANTINED")
        tool_name = str(request.parameters.get("tool") or "")
        if not any(t["name"] == tool_name for t in live_tools):
            raise ToolGatewayError("MCP_TOOL_UNKNOWN")
        model_parameters = request.parameters.get("parameters") or {}
        if not isinstance(model_parameters, dict):
            raise ToolGatewayError("MCP_PARAMETERS_INVALID")
        try:
            call_result = call_tool(endpoint=row["endpoint"] or "", access_token=access_token,
                                    allowed_domains=domains, tool_name=tool_name, arguments=model_parameters)
        except MCPClientError as exc:
            raise ToolGatewayError(f"EXTERNAL_TOOL_FAILED:{exc}") from exc
        except Exception as exc:  # raw transport errors escape the adapter's taxonomy
            raise ToolGatewayError(f"EXTERNAL_TOOL_FAILED:{type(exc).__name__}") from exc
        payload = {"content": safe_markdown(call_result["content"]), "is_error": call_result["is_error"]}
        return GatewayResult(
            descriptor_id=request.descriptor_id, outcome="untrusted_read", payload=payload,
            correlation_id=correlation_id,
            trace=[{"correlation_id": correlation_id, "descriptor": request.descriptor_id,
                    "grant_recheck": "passed", "binding_recheck": "passed",
                    "schema_recheck": "passed", "tool": tool_name}],
        )

    def _execute_skill(self, request: GatewayRequest, correlation_id: str) -> GatewayResult:
        from app.services.skills import (
            manifest_canonical_hash, validate_skill_manifest, verify_manifest_signature,
        )
        if not self._recheck_grant(request.agent_id, request.user_id, "run"):
            raise ToolGatewayError("AGENT_GRANT_REVOKED")
        agent_version_id = request.parameters.get("agent_version_id")
        skill_version_id = request.parameters.get("skill_version_id")
        binding_alive = self._db.execute(text(
            "SELECT 1 FROM agent_skill_bindings "
            "WHERE agent_version_id = :av AND skill_version_id = :sv"
        ), {"av": agent_version_id, "sv": skill_version_id}).scalar_one_or_none()
        if binding_alive is None:
            raise ToolGatewayError("SKILL_BINDING_REVOKED")
        row = self._db.execute(text(
            "SELECT v.manifest, v.canonical_hash, v.approval_status FROM skill_versions v WHERE v.id = :id"
        ), {"id": skill_version_id}).mappings().one_or_none()
        if row is None or row["approval_status"] != "approved":
            raise ToolGatewayError("SKILL_VERSION_NOT_APPROVED")
        manifest = row["manifest"]
        if isinstance(manifest, str):
            import json
            manifest = json.loads(manifest)
        # dispatch-time integrity recheck — approval and dispatch can never disagree
        if manifest_canonical_hash(manifest) != row["canonical_hash"]:
            raise ToolGatewayError("SKILL_CANONICAL_HASH_MISMATCH")
        if validate_skill_manifest(manifest):
            raise ToolGatewayError("SKILL_MANIFEST_INVALID")
        signatures = self._db.execute(text(
            "SELECT public_key_hex, signature_hex FROM skill_signatures WHERE version_id = :id"
        ), {"id": skill_version_id}).mappings().all()
        if not any(verify_manifest_signature(
                manifest=manifest, public_key_hex=s["public_key_hex"],
                signature_hex=s["signature_hex"]) for s in signatures):
            raise ToolGatewayError("SKILL_SIGNATURE_INVALID")
        tool_alias = str(request.parameters.get("tool") or "")
        tool = next((t for t in (manifest.get("tools") or []) if t.get("alias") == tool_alias), None)
        if tool is None:
            raise ToolGatewayError("SKILL_TOOL_UNKNOWN")
        leaf_descriptor_id = tool["descriptor_id"]
        leaf_parameters = dict(tool.get("parameters") or {})
        model_parameters = request.parameters.get("parameters") or {}
        if not isinstance(model_parameters, dict):
            raise ToolGatewayError("SKILL_PARAMETERS_INVALID")
        leaf_parameters.update(model_parameters)
        if leaf_descriptor_id in EXTERNAL_TOOL_DESCRIPTORS:
            # the runtime — not the model — supplies the agent identity for
            # nested external leaves; a model-supplied value is overwritten
            leaf_parameters["agent_version_id"] = agent_version_id
        # recursive dispatch through the SAME governed execute() — skills can
        # never bypass the gateway; the leaf descriptor's own rechecks run here.
        # Manifest contract for skills bundling external.* leaf descriptors:
        # the manifest MUST carry tool_connection_version_id in that tool's
        # `parameters` (admin responsibility); the runtime injects the
        # skill-level agent_version_id above, so the recursive execute()
        # re-enters _execute_external with both IDs.
        leaf_request = GatewayRequest(
            agent_id=request.agent_id, user_id=request.user_id,
            descriptor_id=leaf_descriptor_id,
            operation="external_tool_call",
            parameters=leaf_parameters,
        )
        ontology_id = leaf_parameters.get("ontology_id")
        leaf_result = self.execute(leaf_request, ontology_id=ontology_id)
        return GatewayResult(
            descriptor_id=request.descriptor_id, outcome=leaf_result.outcome,
            payload={"skill_tool": tool_alias, "leaf_descriptor_id": leaf_descriptor_id,
                     **leaf_result.payload},
            correlation_id=correlation_id,
            trace=[{"correlation_id": correlation_id, "descriptor": request.descriptor_id,
                    "grant_recheck": "passed", "binding_recheck": "passed",
                    "signature_recheck": "passed", "leaf_descriptor": leaf_descriptor_id}],
        )

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
