"""Governed External MCP Client adapter (P7D external tools, Section 8/10).

Speaks the Streamable HTTP transport's synchronous JSON-response mode only
(single POST -> single JSON body; no SSE, no session resumption, no local
stdio) — a documented scope boundary, not a partial implementation. Every
call goes through the SSRF guard and the connection version's domain
allowlist (fail-closed, same discipline as the Playwright adapter). Never
called directly by the model — only through ToolGateway
(see app/services/tool_gateway.py)."""
from __future__ import annotations

import hashlib
import json

from app.services.tools.playwright import _domain_allowed
from app.services.tools.ssrf_guard import SsrfBlockedError, safe_post


class MCPClientError(Exception):
    """An External MCP call failed or was rejected."""


def _rpc_call(*, endpoint: str, access_token: str, allowed_domains: list[str],
             method: str, params: dict, timeout_seconds: float) -> dict:
    if not endpoint:
        raise MCPClientError("MCP_ENDPOINT_MISSING")
    if not _domain_allowed(endpoint, allowed_domains):
        raise MCPClientError(f"MCP_DOMAIN_NOT_ALLOWED:{endpoint}")
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else None
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        response = safe_post(endpoint, timeout_seconds=timeout_seconds, max_bytes=1_000_000,
                             json_body=body, headers=headers)
    except SsrfBlockedError as exc:
        raise MCPClientError(f"MCP_BLOCKED:{exc}") from exc
    if response.status_code != 200:
        raise MCPClientError(f"MCP_UPSTREAM_ERROR:{response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise MCPClientError("MCP_UPSTREAM_INVALID_JSON") from exc
    if not isinstance(payload, dict):
        raise MCPClientError("MCP_UPSTREAM_INVALID_JSON")
    if "error" in payload:
        error = payload["error"] if isinstance(payload["error"], dict) else {}
        raise MCPClientError(f"MCP_RPC_ERROR:{error.get('message', 'unknown')}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise MCPClientError("MCP_RPC_RESULT_MISSING")
    return result


def list_tools(*, endpoint: str, access_token: str, allowed_domains: list[str],
               timeout_seconds: float = 10.0) -> list[dict]:
    result = _rpc_call(endpoint=endpoint, access_token=access_token, allowed_domains=allowed_domains,
                       method="tools/list", params={}, timeout_seconds=timeout_seconds)
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise MCPClientError("MCP_TOOLS_LIST_INVALID")
    parsed = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise MCPClientError("MCP_TOOLS_LIST_INVALID")
        parsed.append({
            "name": tool["name"],
            "description": str(tool.get("description") or ""),
            "input_schema": tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {},
        })
    return parsed


def call_tool(*, endpoint: str, access_token: str, allowed_domains: list[str],
              tool_name: str, arguments: dict, timeout_seconds: float = 20.0) -> dict:
    result = _rpc_call(endpoint=endpoint, access_token=access_token, allowed_domains=allowed_domains,
                       method="tools/call", params={"name": tool_name, "arguments": arguments},
                       timeout_seconds=timeout_seconds)
    content_blocks = result.get("content")
    texts = []
    if isinstance(content_blocks, list):
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
    return {"content": "\n".join(texts), "is_error": bool(result.get("isError", False))}


def tools_schema_hash(tools: list[dict]) -> str:
    canonical = json.dumps(tools, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
