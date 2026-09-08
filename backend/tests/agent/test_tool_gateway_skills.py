"""P7C: gateway skill dispatch with dispatch-time integrity rechecks."""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
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
    schema = "p7c_gwskill_" + uuid.uuid4().hex
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
    from app.services.agent.configuration import bind_external_tool
    from app.services.tool_connections import (
        approve_connection_version, create_connection, create_connection_version, create_provider,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://search.example.com/v1")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    bind_external_tool(session, actor_id="u-1", agent_version_id="av-1",
                       tool_connection_version_id=version["id"], alias="search")
    return version["id"]


def _bound_skill_version(session, manifest: dict | None = None) -> str:
    from app.services.agent.configuration import bind_skill
    from app.services.skills import manifest_canonical_hash
    from app.services.skills.admin import approve_skill_version, create_package, create_skill_version
    key = Ed25519PrivateKey.generate()
    package = create_package(session, actor_id="u-1", name="supplier-skill")
    if manifest is None:
        manifest = {
            "name": "supplier-skill", "description": "read supplier instances", "instructions": "i",
            "tools": [{"alias": "read_suppliers", "descriptor_id": "ontology.read_instances",
                       "description": "read supplier instances",
                       "parameters": {"query": "stale-default"}}],
        }
    signature = key.sign(bytes.fromhex(manifest_canonical_hash(manifest)))
    version = create_skill_version(session, actor_id="u-1", package_id=package["id"],
                                   manifest=manifest,
                                   signatures=[{"public_key_hex": key.public_key().public_bytes_raw().hex(),
                                                "signature_hex": signature.hex()}])
    approve_skill_version(session, actor_id="u-1", version_id=version["id"])
    bind_skill(session, actor_id="u-1", agent_version_id="av-1",
               skill_version_id=version["id"], alias="skill")
    return version["id"]


def test_gateway_dispatches_skill_to_ontology_leaf(session, monkeypatch):
    from app.services.tool_gateway import GatewayRequest, ToolGateway
    # patch target: _dispatch resolves execute_ontology_read from
    # app.services.ontology_tools at each call (function-local from-import)
    captured = {}

    def _fake_ontology_read(db, *, descriptor_id, parameters, correlation_id):
        captured.update(parameters)
        return ("read", {"items": [], "correlation_id": correlation_id})

    monkeypatch.setattr("app.services.ontology_tools.execute_ontology_read", _fake_ontology_read)
    version_id = _bound_skill_version(session)
    gateway = ToolGateway(session)
    result = gateway.execute(GatewayRequest(
        agent_id="ag-1", user_id="u-1", descriptor_id="external.skill", operation="external_tool_call",
        parameters={"agent_version_id": "av-1", "skill_version_id": version_id,
                    "tool": "read_suppliers", "parameters": {"query": "安全线"}},
    ))
    assert result.outcome == "read"
    assert result.payload["skill_tool"] == "read_suppliers"
    assert result.payload["leaf_descriptor_id"] == "ontology.read_instances"
    assert captured.get("query") == "安全线"  # model params override manifest defaults


def test_gateway_skill_nested_external_leaf_gets_agent_version(session, monkeypatch):
    """Skills bundling external.* leaves: the runtime — not the model — injects
    the request-level agent_version_id into the nested leaf dispatch. Without
    the injection the nested execute() fails closed with
    EXTERNAL_TOOL_BINDING_REVOKED because agent_version_id is None."""
    from app.services.tool_gateway import GatewayRequest, ToolGateway
    search_version_id = _bound_search_version(session)
    version_id = _bound_skill_version(session, manifest={
        "name": "web-skill", "description": "search the web", "instructions": "i",
        "tools": [{"alias": "web", "descriptor_id": "external.search",
                   "description": "search the web",
                   "parameters": {"tool_connection_version_id": search_version_id,
                                  "query": "安全线"}}],
    })
    captured = {}

    def _fake_web_search(*, endpoint, api_key, query, result_limit=5, timeout_seconds=10.0):
        captured["query"] = query
        from app.services.untrusted_artifact import make_artifact
        return [{"title": "Result", "url": "https://x.example.com",
                 "artifact": make_artifact(source="https://x.example.com", media_type="text/plain",
                                           raw_content="ok")}]

    monkeypatch.setattr("app.services.tools.search.web_search", _fake_web_search)
    gateway = ToolGateway(session)
    result = gateway.execute(GatewayRequest(
        agent_id="ag-1", user_id="u-1", descriptor_id="external.skill", operation="external_tool_call",
        parameters={"agent_version_id": "av-1", "skill_version_id": version_id,
                    "tool": "web", "parameters": {}},
    ))
    assert result.outcome == "untrusted_read"
    assert result.payload["skill_tool"] == "web"
    assert result.payload["leaf_descriptor_id"] == "external.search"
    assert captured.get("query") == "安全线"  # manifest defaults carried into the nested dispatch


def test_gateway_rejects_unknown_skill_tool(session):
    from app.services.tool_gateway import GatewayRequest, ToolGateway, ToolGatewayError
    version_id = _bound_skill_version(session)
    gateway = ToolGateway(session)
    with pytest.raises(ToolGatewayError):
        gateway.execute(GatewayRequest(
            agent_id="ag-1", user_id="u-1", descriptor_id="external.skill", operation="external_tool_call",
            parameters={"agent_version_id": "av-1", "skill_version_id": version_id,
                        "tool": "not_in_manifest", "parameters": {}},
        ))


def test_gateway_rejects_revoked_skill_binding(session):
    from app.services.tool_gateway import GatewayRequest, ToolGateway, ToolGatewayError
    from app.services.agent.configuration import unbind_skill
    version_id = _bound_skill_version(session)
    unbind_skill(session, actor_id="u-1", agent_version_id="av-1", alias="skill")
    gateway = ToolGateway(session)
    with pytest.raises(ToolGatewayError):
        gateway.execute(GatewayRequest(
            agent_id="ag-1", user_id="u-1", descriptor_id="external.skill", operation="external_tool_call",
            parameters={"agent_version_id": "av-1", "skill_version_id": version_id,
                        "tool": "read_suppliers", "parameters": {}},
        ))


def test_gateway_rejects_tampered_signature(session):
    from app.services.tool_gateway import GatewayRequest, ToolGateway, ToolGatewayError
    version_id = _bound_skill_version(session)
    # corrupt the stored signature AFTER approval — dispatch must re-verify
    session.execute(text(
        "UPDATE skill_signatures SET signature_hex = :bad WHERE version_id = :vid"
    ), {"bad": "00" * 64, "vid": version_id})
    session.commit()
    gateway = ToolGateway(session)
    with pytest.raises(ToolGatewayError):
        gateway.execute(GatewayRequest(
            agent_id="ag-1", user_id="u-1", descriptor_id="external.skill", operation="external_tool_call",
            parameters={"agent_version_id": "av-1", "skill_version_id": version_id,
                        "tool": "read_suppliers", "parameters": {}},
        ))
