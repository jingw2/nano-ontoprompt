# backend/tests/agent/test_tool_gateway_mcp.py
"""P7D: Tool Gateway dispatch for external.mcp — quarantine, scope, token rechecks."""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR, env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def session():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p7d_gw_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0015_external_mcp").returncode == 0
    s = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','a','a@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    # model chain: agent_versions.default_model_config_version_id is NOT NULL
    s.execute(text(
        "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at) "
        "VALUES ('mc-1','m','llm',NULL,'','openai','[]'::json,'{}'::json,'u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_at) "
        "VALUES ('mcv-1', 'mc-1', 1, 'openai', '{}'::json, :hash, '[]'::json, now())"
    ), {"hash": "0" * 64})
    # application-state schema: 0005 seeds chat-v1 with an active version
    app_schema_version_id = s.execute(text(
        "SELECT active_version_id FROM application_state_schema_registries WHERE application_key = 'chat-v1'"
    )).scalar_one()
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('a-1','private','active','u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, application_state_schema_version_id, config_hash, created_by, created_at) "
        "VALUES ('v-1', 'a-1', 1, 'test-version', 'mcv-1', 'test-model', '', :svid, 'h', 'u-1', now())"
    ), {"svid": app_schema_version_id})
    s.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, "
        "created_by, created_at, updated_at) "
        "VALUES ('grant-1', 'a-1', 'u-1', '[\"run\"]', 1, 'active', 'u-1', now(), now())"
    ))
    s.commit()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _bound_mcp_version(session, *, scope=("ontology:query",), token_scope=("ontology:query",),
                       expires_delta=timedelta(hours=1)) -> str:
    from app.services.agent.configuration import bind_external_tool
    from app.services.encryption_service import encrypt
    from app.services.tool_connections import approve_connection_version, create_connection, create_connection_version, create_provider
    provider = create_provider(session, actor_id="u-1", name="mcp-provider", kind="external_mcp")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(
        session, actor_id="u-1", connection_id=connection["id"], endpoint="https://mcp.example.com/rpc",
        scopes=list(scope), allowlists={"domains": ["mcp.example.com"]})
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    bind_external_tool(session, actor_id="u-1", agent_version_id="v-1",
                       tool_connection_version_id=version["id"], alias="mcp1")
    session.execute(text(
        "INSERT INTO mcp_connection_schemas (id, connection_version_id, tool_schema_hash, tools, "
        "quarantined, pinned_by, pinned_at) VALUES (:id, :cv, 'pinned-hash', "
        "CAST(:tools AS json), false, 'u-1', now())"
    ), {"id": str(uuid.uuid4()), "cv": version["id"],
        "tools": '[{"name": "read_suppliers", "description": "d", "input_schema": {}}]'})
    session.execute(text(
        "INSERT INTO mcp_oauth_tokens (id, connection_version_id, encrypted_access_token, "
        "scope, expires_at, issued_by, rotated_at) VALUES (:id, :cv, :tok, CAST(:scope AS json), "
        ":exp, 'u-1', now())"
    ), {"id": str(uuid.uuid4()), "cv": version["id"], "tok": encrypt("tok-1"),
        "scope": _json(list(token_scope)), "exp": datetime.now(timezone.utc) + expires_delta})
    session.commit()
    return version["id"]


def _json(value):
    import json
    return json.dumps(value)


def test_dispatch_calls_pinned_tool(session, monkeypatch):
    from app.services.tool_gateway import GatewayRequest, ToolGateway
    version_id = _bound_mcp_version(session)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: [
        {"name": "read_suppliers", "description": "d", "input_schema": {}}])
    monkeypatch.setattr("app.services.tools.mcp_client.tools_schema_hash", lambda tools: "pinned-hash")
    monkeypatch.setattr("app.services.tools.mcp_client.call_tool",
                        lambda **kw: {"content": "华东供应商", "is_error": False})
    gateway = ToolGateway(session)
    result = gateway.execute(GatewayRequest(
        agent_id="a-1", user_id="u-1", descriptor_id="external.mcp", operation="external_tool_call",
        parameters={"agent_version_id": "v-1", "tool_connection_version_id": version_id,
                   "tool": "read_suppliers", "parameters": {"query": "华东"}}))
    assert result.outcome == "untrusted_read"
    assert "华东供应商" in result.payload["content"]


def test_dispatch_quarantines_on_schema_drift(session, monkeypatch):
    from app.services.tool_gateway import GatewayRequest, ToolGateway, ToolGatewayError
    version_id = _bound_mcp_version(session)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: [
        {"name": "read_suppliers", "description": "DIFFERENT NOW", "input_schema": {}}])
    monkeypatch.setattr("app.services.tools.mcp_client.tools_schema_hash", lambda tools: "drifted-hash")
    gateway = ToolGateway(session)
    with pytest.raises(ToolGatewayError, match="TOOL_SCHEMA_QUARANTINED"):
        gateway.execute(GatewayRequest(
            agent_id="a-1", user_id="u-1", descriptor_id="external.mcp", operation="external_tool_call",
            parameters={"agent_version_id": "v-1", "tool_connection_version_id": version_id,
                       "tool": "read_suppliers", "parameters": {}}))
    quarantined = session.execute(text(
        "SELECT quarantined FROM mcp_connection_schemas WHERE connection_version_id = :id"
    ), {"id": version_id}).scalar_one()
    assert quarantined is True


def test_dispatch_rejects_already_quarantined_without_live_call(session, monkeypatch):
    from app.services.tool_gateway import GatewayRequest, ToolGateway, ToolGatewayError
    version_id = _bound_mcp_version(session)
    session.execute(text(
        "UPDATE mcp_connection_schemas SET quarantined = true WHERE connection_version_id = :id"
    ), {"id": version_id})
    session.commit()
    called = {"n": 0}
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools",
                        lambda **kw: called.__setitem__("n", called["n"] + 1) or [])
    gateway = ToolGateway(session)
    with pytest.raises(ToolGatewayError, match="TOOL_SCHEMA_QUARANTINED"):
        gateway.execute(GatewayRequest(
            agent_id="a-1", user_id="u-1", descriptor_id="external.mcp", operation="external_tool_call",
            parameters={"agent_version_id": "v-1", "tool_connection_version_id": version_id,
                       "tool": "read_suppliers", "parameters": {}}))
    assert called["n"] == 0  # fail-fast: no network call for an already-quarantined connection


def test_dispatch_rejects_expired_token(session, monkeypatch):
    from app.services.tool_gateway import GatewayRequest, ToolGateway, ToolGatewayError
    version_id = _bound_mcp_version(session, expires_delta=timedelta(hours=-1))
    gateway = ToolGateway(session)
    with pytest.raises(ToolGatewayError, match="MCP_TOKEN_EXPIRED"):
        gateway.execute(GatewayRequest(
            agent_id="a-1", user_id="u-1", descriptor_id="external.mcp", operation="external_tool_call",
            parameters={"agent_version_id": "v-1", "tool_connection_version_id": version_id,
                       "tool": "read_suppliers", "parameters": {}}))


def test_dispatch_rejects_scope_narrower_than_declared(session, monkeypatch):
    from app.services.tool_gateway import GatewayRequest, ToolGateway, ToolGatewayError
    version_id = _bound_mcp_version(session, scope=("ontology:query", "ontology:action"),
                                    token_scope=("ontology:query",))
    gateway = ToolGateway(session)
    with pytest.raises(ToolGatewayError, match="OAUTH_SCOPE_DENIED"):
        gateway.execute(GatewayRequest(
            agent_id="a-1", user_id="u-1", descriptor_id="external.mcp", operation="external_tool_call",
            parameters={"agent_version_id": "v-1", "tool_connection_version_id": version_id,
                       "tool": "read_suppliers", "parameters": {}}))


def test_dispatch_rejects_unpinned_tool_name(session, monkeypatch):
    from app.services.tool_gateway import GatewayRequest, ToolGateway, ToolGatewayError
    version_id = _bound_mcp_version(session)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: [
        {"name": "read_suppliers", "description": "d", "input_schema": {}}])
    monkeypatch.setattr("app.services.tools.mcp_client.tools_schema_hash", lambda tools: "pinned-hash")
    gateway = ToolGateway(session)
    with pytest.raises(ToolGatewayError, match="MCP_TOOL_UNKNOWN"):
        gateway.execute(GatewayRequest(
            agent_id="a-1", user_id="u-1", descriptor_id="external.mcp", operation="external_tool_call",
            parameters={"agent_version_id": "v-1", "tool_connection_version_id": version_id,
                       "tool": "delete_everything", "parameters": {}}))
