"""Ontology MCP Server tool registry and dispatch (P7E plan 2).

The legacy `ontology_*` tools are dispatched directly against
ontology_query.py's grant-checked read functions and
mcp_write_requests.py's write-approval flow — NOT through ToolGateway,
which is Agent-Turn/descriptor-registry oriented and doesn't fit a direct
external OAuth client either (the same "new lightweight MCP-native path"
reasoning that shaped the write-approval schema — see the plan's Global
Constraints). They remain compatibility adapters only: unchanged from
before Task 18, still scoped by `_require_scope`, still using the verified
`OAuthContext` identity for everything they do, never a caller-supplied
argument.

Task 18 adds `call_runtime_tool`, a second dispatch entry point for the
`runtime_*` tool names — MCP's adapter onto the same `RuntimeService`
(Task 15) that REST (Task 16) and the SDK (Task 17) call. It builds a
request model from caller-supplied arguments only (never from a caller-
asserted `agent_id`/`user_id`, which the shared request models don't even
have a field for) and calls `RuntimeService` directly; no policy or query
logic is duplicated here.
"""
from __future__ import annotations

from typing import Any, Mapping

from pydantic import ValidationError

from app.deps.oauth import OAuthContext
from app.schemas.runtime import InvestigationRequest
from app.services import mcp_write_requests
from app.services.mcp_write_requests import McpWriteRequestError
from app.services.ontology_query import query_instances, query_relations
from app.services.runtime.credentials import RuntimeAccessError, RuntimeContext
from app.services.runtime.service import ActionPlan, ActionPlanRequest, RuntimeService

TOOLS = [
    {
        "name": "ontology_read_instances",
        "description": "Read entity instances from a published ontology release.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ontology_id": {"type": "string"},
                "release_id": {"type": "string"},
                "entity_id": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["ontology_id", "release_id"],
        },
    },
    {
        "name": "ontology_traverse_relations",
        "description": "Traverse relations from an entity instance in a published ontology release.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ontology_id": {"type": "string"},
                "release_id": {"type": "string"},
                "instance_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["ontology_id", "release_id", "instance_id"],
        },
    },
    {
        "name": "ontology_propose_write",
        "description": "Propose a write action for human approval. Never applies immediately, even once approved.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ontology_id": {"type": "string"},
                "release_id": {"type": "string"},
                "descriptor_id": {"type": "string"},
                "target_instance_id": {"type": "string"},
                "parameters": {"type": "object"},
            },
            "required": ["ontology_id", "release_id", "descriptor_id", "parameters"],
        },
    },
    {
        "name": "ontology_check_write_status",
        "description": "Check the status (pending/approved/rejected/expired) of a previously proposed write.",
        "inputSchema": {
            "type": "object",
            "properties": {"request_id": {"type": "string"}},
            "required": ["request_id"],
        },
    },
]

_READ_SCOPE = "ontology:read"
_WRITE_SCOPE = "ontology:write"


class McpToolError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _clamp_limit(arguments: dict, *, default: int = 20, maximum: int = 200) -> int:
    raw = arguments.get("limit", default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise McpToolError("INVALID_ARGUMENT", "limit must be an integer")
    return max(1, min(value, maximum))


def list_tools() -> list[dict]:
    return TOOLS


def _require_scope(ctx: OAuthContext, scope: str) -> None:
    if scope not in ctx.scope:
        raise McpToolError("SCOPE_DENIED", f"token missing required scope: {scope}")


def call_tool(db, ctx: OAuthContext, name: str, arguments: dict) -> dict:
    if name == "ontology_read_instances":
        _require_scope(ctx, _READ_SCOPE)
        try:
            items = query_instances(
                db, ontology_id=arguments["ontology_id"], release_id=arguments["release_id"],
                entity_id=arguments.get("entity_id"), query=arguments.get("query"),
                user_id=ctx.user_id, limit=_clamp_limit(arguments),
            )
        except KeyError as exc:
            raise McpToolError("MISSING_ARGUMENT", str(exc))
        return {"items": items}
    if name == "ontology_traverse_relations":
        _require_scope(ctx, _READ_SCOPE)
        try:
            items = query_relations(
                db, ontology_id=arguments["ontology_id"], release_id=arguments["release_id"],
                instance_id=arguments["instance_id"], user_id=ctx.user_id, limit=_clamp_limit(arguments),
            )
        except KeyError as exc:
            raise McpToolError("MISSING_ARGUMENT", str(exc))
        return {"items": items}
    if name == "ontology_propose_write":
        _require_scope(ctx, _WRITE_SCOPE)
        try:
            return mcp_write_requests.create_write_request(
                db, oauth_client_id=ctx.client_id, user_id=ctx.user_id,
                ontology_id=arguments["ontology_id"], release_id=arguments["release_id"],
                descriptor_id=arguments["descriptor_id"], parameters=arguments.get("parameters", {}),
                target_instance_id=arguments.get("target_instance_id"),
            )
        except KeyError as exc:
            raise McpToolError("MISSING_ARGUMENT", str(exc))
        except McpWriteRequestError as exc:
            raise McpToolError(str(exc), f"write proposal rejected: {exc}")
    if name == "ontology_check_write_status":
        _require_scope(ctx, _WRITE_SCOPE)
        try:
            request_id = arguments["request_id"]
        except KeyError as exc:
            raise McpToolError("MISSING_ARGUMENT", str(exc))
        item = mcp_write_requests.get_write_request(
            db, request_id=request_id, user_id=ctx.user_id, oauth_client_id=ctx.client_id,
        )
        if item is None:
            raise McpToolError("NOT_FOUND", "unknown write request")
        return {"status": item["status"], "resolved_at": str(item["resolved_at"]) if item["resolved_at"] else None}
    raise McpToolError("TOOL_UNKNOWN", f"unknown tool: {name}")


# --------------------------------------------------------- Task 18: runtime

_runtime_service = RuntimeService()

_INVESTIGATE_FIELDS = frozenset({"semantic_snapshot_id", "query", "ontology_id", "entity_type", "filters", "limit"})
_ACTION_PLAN_FIELDS = frozenset({"semantic_snapshot_id", "action_id", "parameters", "target_selector", "idempotency_key"})


def _investigation_request_from_arguments(arguments: Mapping[str, object]) -> InvestigationRequest:
    """Build an `InvestigationRequest` from caller arguments only — never a
    caller-asserted `agent_id`/`user_id` (silently dropped, same as any
    other unrecognized key), and a legacy `release_id`-only call (no
    `semantic_snapshot_id`) is passed straight through as the snapshot id so
    it fails the same `SNAPSHOT_NOT_GOVERNED` way `RuntimeService` already
    fails a caller asserting a release id where a snapshot id belongs — not
    a validation error."""
    payload: dict[str, Any] = {k: v for k, v in arguments.items() if k in _INVESTIGATE_FIELDS}
    if "semantic_snapshot_id" not in payload:
        legacy_release_id = arguments.get("release_id")
        if legacy_release_id is None:
            raise McpToolError("MISSING_ARGUMENT", "semantic_snapshot_id is required")
        payload["semantic_snapshot_id"] = legacy_release_id
    payload.setdefault("ontology_id", "")
    try:
        return InvestigationRequest(**payload)
    except ValidationError as exc:
        raise McpToolError("INVALID_ARGUMENT", str(exc)) from exc


def _action_plan_request_from_arguments(arguments: Mapping[str, object]) -> ActionPlanRequest:
    """Build an `ActionPlanRequest` from caller arguments only — same
    caller-identity exclusion as `_investigation_request_from_arguments`."""
    payload: dict[str, Any] = {k: v for k, v in arguments.items() if k in _ACTION_PLAN_FIELDS}
    try:
        return ActionPlanRequest(
            semantic_snapshot_id=payload["semantic_snapshot_id"],
            action_id=payload["action_id"],
            parameters=payload.get("parameters", {}),
            target_selector=payload.get("target_selector"),
            **({"idempotency_key": payload["idempotency_key"]} if "idempotency_key" in payload else {}),
        )
    except KeyError as exc:
        raise McpToolError("MISSING_ARGUMENT", str(exc)) from exc


def _plan_to_dict(plan: ActionPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "semantic_snapshot_id": plan.semantic_snapshot_id,
        "ontology_release_id": plan.ontology_release_id,
        "agent_id": plan.agent_id,
        "user_id": plan.user_id,
        "action_id": plan.action_id,
        "parameters": dict(plan.parameters),
        "target_key": list(plan.target_key),
        "before_image_hash": plan.before_image_hash,
        "version_hash": plan.version_hash,
        "predicted_diff": dict(plan.predicted_diff),
        "impact_scope": dict(plan.impact_scope),
        "risk_classification": plan.risk_classification,
        "policy_decision": dict(plan.policy_decision),
        "precondition_hashes": list(plan.precondition_hashes),
        "expiry": plan.expiry.isoformat(),
        "idempotency_key": plan.idempotency_key,
        "plan_hash": plan.plan_hash,
    }


def call_runtime_tool(
    db, context: RuntimeContext, name: str, arguments: Mapping[str, object],
) -> Mapping[str, object]:
    """Dispatch the `runtime_*` tool names onto `RuntimeService`.

    Every tool maps to exactly one `RuntimeService` method; `context` is
    always the verified `RuntimeContext` (bridged from the MCP OAuth
    dependency by `app.deps.oauth.get_runtime_context`), never anything the
    caller's own `arguments` assert.
    """
    if name == "runtime_investigate":
        request = _investigation_request_from_arguments(arguments)
        try:
            result = _runtime_service.investigate(request, context, db)
        except RuntimeAccessError as exc:
            raise McpToolError(exc.reason_code, str(exc)) from exc
        # `agent_id`/`user_id` reflect the verified `context.principal` — the
        # same identity every field of `result` was computed against — never
        # anything from `arguments`, so a caller-asserted agent_id/user_id
        # (silently dropped when building `request` above) can never surface
        # here as if it had been honored.
        return {
            **result.model_dump(mode="json"),
            "agent_id": context.principal.agent_id,
            "user_id": context.principal.user_id,
        }
    if name == "runtime_create_action_plan":
        request = _action_plan_request_from_arguments(arguments)
        try:
            plan = _runtime_service.create_action_plan(request, context, db)
        except RuntimeAccessError as exc:
            raise McpToolError(exc.reason_code, str(exc)) from exc
        return _plan_to_dict(plan)
    if name in ("runtime_get_action_plan", "runtime_get_execution_status"):
        plan_id = arguments.get("plan_id")
        if not plan_id:
            raise McpToolError("MISSING_ARGUMENT", "plan_id is required")
        try:
            plan = _runtime_service.get_action_plan(str(plan_id), context, db)
        except RuntimeAccessError as exc:
            raise McpToolError(exc.reason_code, str(exc)) from exc
        if name == "runtime_get_execution_status":
            # Mirrors `app.routers.v2.runtime.get_execution_status`: Phase 3
            # execution does not exist yet, so a resolvable, principal-owned
            # plan always reports the same stable "not started" status,
            # visibility-checked through the same `get_action_plan` call.
            return {"plan_id": plan.id, "status": "not_started", "correlation_id": context.correlation_id}
        return _plan_to_dict(plan)
    raise McpToolError("TOOL_UNKNOWN", f"unknown tool: {name}")
