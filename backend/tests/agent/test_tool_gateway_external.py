"""P7A: Tool Gateway external-tool dispatch."""
import os
import subprocess
import sys
import uuid
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
    schema = "p7a_gw_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0013_external_tool_alias_unique").returncode == 0
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
        "VALUES ('ag-1','private','active','u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, application_state_schema_version_id, config_hash, created_by, created_at) "
        "VALUES ('av-1', 'ag-1', 1, 'test-version', 'mcv-1', 'test-model', '', :svid, 'h', 'u-1', now())"
    ), {"svid": app_schema_version_id})
    s.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, "
        "created_by, created_at, updated_at) "
        "VALUES ('grant-1', 'ag-1', 'u-1', '[\"run\"]'::json, 1, 'active', 'u-1', now(), now())"
    ))
    s.commit()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _bound_search_version(session) -> str:
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version, create_provider,
    )
    from app.services.agent.configuration import bind_external_tool
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://search.example.com/v1")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    bind_external_tool(session, actor_id="u-1", agent_version_id="av-1",
                       tool_connection_version_id=version["id"], alias="search")
    return version["id"]


def test_gateway_dispatches_search_and_wraps_results(session, monkeypatch):
    from app.services.tool_gateway import GatewayRequest, ToolGateway

    def _fake_web_search(*, endpoint, api_key, query, result_limit=5, timeout_seconds=10.0):
        from app.services.untrusted_artifact import make_artifact
        return [{"title": "Result", "url": "https://x.example.com",
                 "artifact": make_artifact(source="https://x.example.com", media_type="text/plain",
                                           raw_content="<b>hi</b>")}]

    monkeypatch.setattr("app.services.tools.search.web_search", _fake_web_search)
    version_id = _bound_search_version(session)
    gateway = ToolGateway(session)
    result = gateway.execute(GatewayRequest(
        agent_id="ag-1", user_id="u-1", descriptor_id="external.search", operation="external_tool_call",
        parameters={"agent_version_id": "av-1", "tool_connection_version_id": version_id, "query": "hi"},
    ))
    assert result.outcome == "untrusted_read"
    assert result.payload["results"][0]["content"] == "hi"  # sanitized, script/tags stripped upstream


def test_gateway_sanitizes_title_and_url_at_payload_boundary(session, monkeypatch):
    """Malicious upstream `title`/`url` must not reach the model context:
    title goes through Safe Markdown, non-http(s) urls collapse to ''."""
    from app.services.tool_gateway import GatewayRequest, ToolGateway

    def _fake_web_search(*, endpoint, api_key, query, result_limit=5, timeout_seconds=10.0):
        from app.services.untrusted_artifact import make_artifact
        return [{"title": "<img src=x onerror=1>", "url": "javascript:alert(1)",
                 "artifact": make_artifact(source="https://x.example.com", media_type="text/plain",
                                           raw_content="ok")}]

    monkeypatch.setattr("app.services.tools.search.web_search", _fake_web_search)
    version_id = _bound_search_version(session)
    gateway = ToolGateway(session)
    result = gateway.execute(GatewayRequest(
        agent_id="ag-1", user_id="u-1", descriptor_id="external.search", operation="external_tool_call",
        parameters={"agent_version_id": "av-1", "tool_connection_version_id": version_id, "query": "hi"},
    ))
    entry = result.payload["results"][0]
    assert "<" not in entry["title"]  # <img ...> stripped by Safe Markdown
    assert entry["url"] == ""  # javascript: scheme blocked


def test_gateway_rejects_revoked_binding(session, monkeypatch):
    from app.services.tool_gateway import GatewayRequest, ToolGateway, ToolGatewayError
    from app.services.agent.configuration import unbind_external_tool
    version_id = _bound_search_version(session)
    unbind_external_tool(session, actor_id="u-1", agent_version_id="av-1", alias="search")
    gateway = ToolGateway(session)
    with pytest.raises(ToolGatewayError):
        gateway.execute(GatewayRequest(
            agent_id="ag-1", user_id="u-1", descriptor_id="external.search", operation="external_tool_call",
            parameters={"agent_version_id": "av-1", "tool_connection_version_id": version_id, "query": "hi"},
        ))


def test_gateway_rejects_unapproved_version(session):
    from app.services.tool_gateway import GatewayRequest, ToolGateway, ToolGatewayError
    from app.services.tool_connections import create_connection, create_connection_version, create_provider
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"])
    # force the binding row directly since bind_external_tool would itself reject this
    session.execute(text(
        "INSERT INTO agent_external_tool_bindings (id, agent_version_id, tool_connection_version_id, alias, created_at) "
        "VALUES ('b-1', 'av-1', :v, 'search', now())"
    ), {"v": version["id"]})
    session.commit()
    gateway = ToolGateway(session)
    with pytest.raises(ToolGatewayError):
        gateway.execute(GatewayRequest(
            agent_id="ag-1", user_id="u-1", descriptor_id="external.search", operation="external_tool_call",
            parameters={"agent_version_id": "av-1", "tool_connection_version_id": version["id"], "query": "hi"},
        ))
