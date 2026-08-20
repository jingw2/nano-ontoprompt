"""Ontology MCP Server — JSON-RPC 2.0 over a single stateless POST endpoint,
matching the Streamable HTTP synchronous-JSON-response mode this codebase's
own MCP client (app/services/tools/mcp_client.py) already speaks. No SSE, no
session resumption — `initialize` is a pure capability-announcement call
with no server-side session state, matching this server's stateless design.
"""
import json

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.deps import get_db
from app.deps.oauth import OAuthContext, get_oauth_context
from app.services import mcp_tools
from app.services.mcp_tools import McpToolError

router = APIRouter()

MCP_PROTOCOL_VERSION = "2024-11-05"


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


@router.post("/mcp")
def mcp_rpc(
    body: dict,
    db: Session = Depends(get_db),
    ctx: OAuthContext = Depends(get_oauth_context),
):
    request_id = body.get("id")
    if request_id is None:
        return Response(status_code=202)
    method = body.get("method")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, -32602, "params must be an object")
    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "nano-ontoprompt-ontology-mcp", "version": "1.0.0"},
        })
    if method == "tools/list":
        return _result(request_id, {"tools": mcp_tools.list_tools()})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "arguments must be an object")
        try:
            output = mcp_tools.call_tool(db, ctx, name, arguments)
        except McpToolError as exc:
            return _result(request_id, {
                "content": [{"type": "text", "text": f"{exc.code}: {exc.message}"}],
                "isError": True,
            })
        except Exception:
            return _error(request_id, -32603, "internal error")
        return _result(request_id, {
            "content": [{"type": "text", "text": json.dumps(output, default=str)}],
            "isError": False,
        })
    return _error(request_id, -32601, f"method not found: {method}")
