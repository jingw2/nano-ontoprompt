"""P7D: SSRF-safe MCP JSON-RPC client (tools/list, tools/call)."""
import httpx
import pytest

from app.services.tools.mcp_client import MCPClientError, call_tool, list_tools, tools_schema_hash


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_list_tools_parses_result(monkeypatch):
    def fake_safe_post(url, *, timeout_seconds, max_bytes, json_body, headers=None):
        assert json_body["method"] == "tools/list"
        assert headers["Authorization"] == "Bearer tok-1"
        return _FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": {"tools": [
            {"name": "read_suppliers", "description": "d", "inputSchema": {"type": "object"}},
        ]}})

    monkeypatch.setattr("app.services.tools.mcp_client.safe_post", fake_safe_post)
    tools = list_tools(endpoint="https://mcp.example.com/rpc", access_token="tok-1",
                       allowed_domains=["mcp.example.com"])
    assert tools == [{"name": "read_suppliers", "description": "d", "input_schema": {"type": "object"}}]


def test_list_tools_rejects_domain_not_allowed():
    with pytest.raises(MCPClientError, match="MCP_DOMAIN_NOT_ALLOWED"):
        list_tools(endpoint="https://evil.example.com/rpc", access_token="tok-1",
                  allowed_domains=["mcp.example.com"])


def test_call_tool_returns_joined_text_content(monkeypatch):
    def fake_safe_post(url, *, timeout_seconds, max_bytes, json_body, headers=None):
        assert json_body["method"] == "tools/call"
        assert json_body["params"] == {"name": "read_suppliers", "arguments": {"query": "华东"}}
        return _FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": "supplier A"}, {"type": "text", "text": "supplier B"}],
            "isError": False,
        }})

    monkeypatch.setattr("app.services.tools.mcp_client.safe_post", fake_safe_post)
    result = call_tool(endpoint="https://mcp.example.com/rpc", access_token="tok-1",
                       allowed_domains=["mcp.example.com"], tool_name="read_suppliers",
                       arguments={"query": "华东"})
    assert result == {"content": "supplier A\nsupplier B", "is_error": False}


def test_call_tool_surfaces_protocol_error(monkeypatch):
    def fake_safe_post(url, *, timeout_seconds, max_bytes, json_body, headers=None):
        return _FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "not found"}})

    monkeypatch.setattr("app.services.tools.mcp_client.safe_post", fake_safe_post)
    with pytest.raises(MCPClientError, match="MCP_RPC_ERROR:not found"):
        call_tool(endpoint="https://mcp.example.com/rpc", access_token="tok-1",
                 allowed_domains=["mcp.example.com"], tool_name="x", arguments={})


def test_call_tool_wraps_ssrf_block(monkeypatch):
    from app.services.tools.ssrf_guard import SsrfBlockedError

    def fake_safe_post(*args, **kwargs):
        raise SsrfBlockedError("SSRF_BLOCKED_TARGET:evil.example.com:10.0.0.1")

    monkeypatch.setattr("app.services.tools.mcp_client.safe_post", fake_safe_post)
    with pytest.raises(MCPClientError, match="MCP_BLOCKED:SSRF_BLOCKED_TARGET"):
        call_tool(endpoint="https://mcp.example.com/rpc", access_token="tok-1",
                 allowed_domains=["mcp.example.com"], tool_name="x", arguments={})


def test_tools_schema_hash_is_key_order_independent():
    a = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    b = [{"input_schema": {"type": "object"}, "description": "d", "name": "t"}]
    assert tools_schema_hash(a) == tools_schema_hash(b)


def test_tools_schema_hash_detects_drift():
    a = [{"name": "t", "description": "d", "input_schema": {}}]
    b = [{"name": "t", "description": "changed", "input_schema": {}}]
    assert tools_schema_hash(a) != tools_schema_hash(b)
