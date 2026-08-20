"""Ontology MCP Server tool registry and dispatch (P7E plan 2).

Tools are dispatched directly against ontology_query.py's grant-checked
read functions and mcp_write_requests.py's write-approval flow — NOT
through ToolGateway, which is Agent-Turn/descriptor-registry oriented and
doesn't fit a direct external OAuth client either (the same "new
lightweight MCP-native path" reasoning that shaped the write-approval
schema — see the plan's Global Constraints).
"""
from app.deps.oauth import OAuthContext
from app.services import mcp_write_requests
from app.services.mcp_write_requests import McpWriteRequestError
from app.services.ontology_query import query_instances, query_relations

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
