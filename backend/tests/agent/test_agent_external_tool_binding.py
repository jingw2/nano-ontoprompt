"""P7A: Agent external-tool binding write API."""
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
    schema = "p7a_bind_" + uuid.uuid4().hex
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
        "VALUES ('ag-1','private','active','u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, application_state_schema_version_id, config_hash, created_by, created_at) "
        "VALUES ('av-1', 'ag-1', 1, 'test-version', 'mcv-1', 'test-model', '', :svid, 'h', 'u-1', now())"
    ), {"svid": app_schema_version_id})
    s.commit()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _approved_version(session) -> str:
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version, create_provider,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://search.example.com/v1")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    return version["id"]


def _active_version(session, kind: str = "search") -> dict:
    """An approved AND activated connection version — what the new catalog
    endpoint should surface as bindable."""
    from app.services.tool_connections import (
        activate_connection_version, approve_connection_version,
        create_connection, create_connection_version, create_provider,
    )
    provider = create_provider(session, actor_id="u-1", name=f"{kind}-provider", kind=kind)
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://example.com/v1")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    activate_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                version_id=version["id"])
    return {"connection_id": connection["id"], "version_id": version["id"],
            "provider_name": provider["name"], "provider_kind": kind}


def test_bind_rejects_unapproved_version(session):
    from app.services.agent.configuration import bind_external_tool, AgentConfigError
    from app.services.tool_connections import create_connection, create_provider
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    from app.services.tool_connections import create_connection_version
    unapproved = create_connection_version(session, actor_id="u-1", connection_id=connection["id"])
    with pytest.raises(AgentConfigError):
        bind_external_tool(session, actor_id="u-1", agent_version_id="av-1",
                           tool_connection_version_id=unapproved["id"], alias="search")


def test_bind_then_unbind(session):
    from app.services.agent.configuration import bind_external_tool, unbind_external_tool
    version_id = _approved_version(session)
    bound = bind_external_tool(session, actor_id="u-1", agent_version_id="av-1",
                               tool_connection_version_id=version_id, alias="search")
    assert bound["alias"] == "search"
    unbind_external_tool(session, actor_id="u-1", agent_version_id="av-1", alias="search")
    remaining = session.execute(text(
        "SELECT count(*) FROM agent_external_tool_bindings WHERE agent_version_id = 'av-1'"
    )).scalar_one()
    assert remaining == 0


def test_duplicate_alias_rejected(session):
    from app.services.agent.configuration import bind_external_tool, AgentConfigError
    version_id = _approved_version(session)
    bind_external_tool(session, actor_id="u-1", agent_version_id="av-1",
                       tool_connection_version_id=version_id, alias="search")
    with pytest.raises(AgentConfigError):
        bind_external_tool(session, actor_id="u-1", agent_version_id="av-1",
                           tool_connection_version_id=version_id, alias="search")


def test_cross_agent_version_rejected(session):
    """A grant on agent A must not reach agent B's immutable version: the
    version-ownership check 404s before any binding mutation."""
    from fastapi.testclient import TestClient

    from app.deps import get_db
    from app.main import app
    from app.services.auth_service import create_access_token

    app_schema_version_id = session.execute(text(
        "SELECT active_version_id FROM application_state_schema_registries WHERE application_key = 'chat-v1'"
    )).scalar_one()
    session.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('ag-2','private','active','u-1',now(),now())"
    ))
    session.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, application_state_schema_version_id, config_hash, created_by, created_at) "
        "VALUES ('av-2', 'ag-2', 1, 'test-version', 'mcv-1', 'test-model', '', :svid, 'h', 'u-1', now())"
    ), {"svid": app_schema_version_id})
    session.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, status, created_by) "
        "VALUES ('aag-1', 'ag-1', 'u-1', '[\"edit\"]'::json, 'active', 'u-1')"
    ))
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'admin'})}"}
            r = client.post("/api/v1/agents/ag-1/versions/av-2/external-tools",
                            json={"tool_connection_version_id": "tcv-x", "alias": "search"},
                            headers={**headers, "Idempotency-Key": "ag-bind-cross-1234567890"})
            assert r.status_code == 404, r.text
            r = client.delete("/api/v1/agents/ag-1/versions/av-2/external-tools/search",
                              headers=headers)
            assert r.status_code == 404, r.text
    finally:
        app.dependency_overrides.clear()


def test_bind_route_rejects_invalid_alias(session):
    """The alias charset/length contract is enforced at the schema boundary:
    a space-containing alias is 422 before any DB work; a valid one binds."""
    from fastapi.testclient import TestClient

    from app.deps import get_db
    from app.main import app
    from app.services.auth_service import create_access_token

    session.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, status, created_by) "
        "VALUES ('aag-1', 'ag-1', 'u-1', '[\"edit\"]'::json, 'active', 'u-1')"
    ))
    session.commit()
    version_id = _approved_version(session)

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'admin'})}"}
            r = client.post("/api/v1/agents/ag-1/versions/av-1/external-tools",
                            json={"tool_connection_version_id": version_id, "alias": "web search"},
                            headers={**headers, "Idempotency-Key": "ag-bind-alias-1234567890"})
            assert r.status_code == 422, r.text
            r = client.post("/api/v1/agents/ag-1/versions/av-1/external-tools",
                            json={"tool_connection_version_id": version_id, "alias": "search"},
                            headers={**headers, "Idempotency-Key": "ag-bind-alias-0987654321"})
            assert r.status_code == 201, r.text
            assert r.json()["data"]["alias"] == "search"
    finally:
        app.dependency_overrides.clear()


def test_unbind_missing_alias_rejected(session):
    from app.services.agent.configuration import AgentConfigError, unbind_external_tool
    with pytest.raises(AgentConfigError):
        unbind_external_tool(session, actor_id="u-1", agent_version_id="av-1", alias="nope")


def test_external_tool_catalog_lists_only_active_approved_live_kinds(session):
    from app.services.agent.catalog import agent_external_tool_catalog
    from app.services.tool_connections import create_connection, create_connection_version, create_provider
    active = _active_version(session, kind="external_mcp")
    # approved connection version that was never activated -> excluded
    provider2 = create_provider(session, actor_id="u-1", name="unactivated", kind="playwright")
    connection2 = create_connection(session, actor_id="u-1", provider_id=provider2["id"])
    create_connection_version(session, actor_id="u-1", connection_id=connection2["id"])
    # active+approved but a non-live kind -> excluded
    _active_version(session, kind="skill")
    items = agent_external_tool_catalog(session)
    assert [i["tool_connection_version_id"] for i in items] == [active["version_id"]]
    assert items[0]["provider_kind"] == "external_mcp"
    assert items[0]["provider_name"] == active["provider_name"]
    assert "credential_reference" not in items[0]


def test_catalog_route_requires_authentication(session):
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.main import app

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/agents/catalog/external-tools")
            assert r.status_code == 403, r.text
    finally:
        app.dependency_overrides.clear()


def test_list_bindings_route_returns_joined_metadata(session):
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.main import app
    from app.services.agent.configuration import bind_external_tool
    from app.services.auth_service import create_access_token

    active = _active_version(session, kind="search")
    bind_external_tool(session, actor_id="u-1", agent_version_id="av-1",
                       tool_connection_version_id=active["version_id"], alias="search")
    session.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, status, created_by) "
        "VALUES ('aag-1', 'ag-1', 'u-1', '[\"view_config\"]'::json, 'active', 'u-1')"
    ))
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'admin'})}"}
            r = client.get("/api/v1/agents/ag-1/versions/av-1/external-tools", headers=headers)
            assert r.status_code == 200, r.text
            items = r.json()["data"]["items"]
            assert len(items) == 1
            assert items[0]["alias"] == "search"
            assert items[0]["provider_kind"] == "search"
            assert items[0]["approval_status"] == "approved"
    finally:
        app.dependency_overrides.clear()


def test_list_bindings_route_hides_cross_agent_version(session):
    """A grant on agent A must not reach agent B's version — same
    existence-hiding contract as the write endpoints (test_cross_agent_version_rejected above)."""
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.main import app
    from app.services.auth_service import create_access_token

    app_schema_version_id = session.execute(text(
        "SELECT active_version_id FROM application_state_schema_registries WHERE application_key = 'chat-v1'"
    )).scalar_one()
    session.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('ag-2','private','active','u-1',now(),now())"
    ))
    session.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, application_state_schema_version_id, config_hash, created_by, created_at) "
        "VALUES ('av-2', 'ag-2', 1, 'test-version', 'mcv-1', 'test-model', '', :svid, 'h', 'u-1', now())"
    ), {"svid": app_schema_version_id})
    session.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, status, created_by) "
        "VALUES ('aag-1', 'ag-1', 'u-1', '[\"view_config\"]'::json, 'active', 'u-1')"
    ))
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'admin'})}"}
            r = client.get("/api/v1/agents/ag-1/versions/av-2/external-tools", headers=headers)
            assert r.status_code == 404, r.text
    finally:
        app.dependency_overrides.clear()
