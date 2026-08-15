"""P2B-API: governed Agent configuration API.

Typed envelopes/cursors, method auth (view_config for reads, edit for writes,
existence-hiding 404), and Idempotency-Key validation on writes.  Catalogs
return only what the principal's grants and role ceiling permit; model
catalog is redacted and excludes blocked/archived identities.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.services.auth_service import create_access_token, hash_password


BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p2b_api_red_contract():
    failures = []
    router = BACKEND_DIR / "app" / "routers" / "agents.py"
    if not router.exists():
        failures.append("missing app/routers/agents.py")
    else:
        source = router.read_text()
        for symbol in ("/versions", "Idempotency-Key", "view_config", "catalog"):
            if symbol not in source:
                failures.append(f"agents router missing {symbol}")
    schemas = BACKEND_DIR / "app" / "schemas" / "agents.py"
    if not schemas.exists():
        failures.append("missing app/schemas/agents.py")
    if failures:
        pytest.fail("RED_P2B_API: " + "; ".join(failures))


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema}', safe='-=')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def ctx():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p2b_api_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0008_agent_tool_selection").returncode == 0
    Session = sessionmaker(bind=create_engine(_scoped_url(schema)))
    with Session() as session:
        editor_id = str(uuid.uuid4())
        viewer_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES (:id,'api-editor','ae@t.com','h','editor',true,:d,now(),now())"
        ), {"id": editor_id, "d": DEFAULT_DOMAIN})
        session.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES (:id,'api-viewer','av@t.com','h','viewer',true,:d,now(),now())"
        ), {"id": viewer_id, "d": DEFAULT_DOMAIN})
        # model identity + active version + a second LLM identity for catalog checks
        session.execute(text(
            "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at) "
            "VALUES (:id,'m','llm',NULL,'','openai','[]'::json,'{}'::json,:owner,now(),now())"
        ), {"id": str(uuid.uuid4()), "owner": editor_id})
        model_id = session.execute(text("SELECT id FROM model_configs LIMIT 1")).scalar_one()
        model_version = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_at) "
            "VALUES (:id, :mc, 1, 'openai', '{}'::json, :hash, '[]'::json, now())"
        ), {"id": model_version, "mc": model_id, "hash": "0" * 64})
        session.execute(text(
            "UPDATE model_configs SET active_version_id = :vid, status = 'active' WHERE id = :mc"
        ), {"vid": model_version, "mc": model_id})
        # a blocked model identity (no active version) — must not appear in the catalog
        session.execute(text(
            "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at,status) "
            "VALUES (:id,'blocked','llm',NULL,'','openai','[]'::json,'{}'::json,:owner,now(),now(),'migration_blocked')"
        ), {"id": str(uuid.uuid4()), "owner": editor_id})
        app_schema = session.execute(text(
            "SELECT v.id FROM application_state_schema_versions v "
            "JOIN application_state_schema_registries r ON r.active_version_id = v.id "
            "WHERE r.application_key = 'chat-v1'"
        )).scalar_one()
        session.commit()
        yield session, editor_id, viewer_id, model_version, app_schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _client(session):
    from app.deps import get_db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


def _create_agent(session, editor_id, model_version, app_schema, name="API Agent"):
    from app.services.agent.configuration import create_agent

    return create_agent(session, actor_id=editor_id, name=name, description="d",
                        default_model_config_version_id=model_version, default_model_name="gpt-4o",
                        system_prompt="p", memory_settings={},
                        application_state_schema_version_id=app_schema)


def test_agent_api_lifecycle_and_method_auth(ctx):
    from fastapi.testclient import TestClient

    session, editor_id, viewer_id, model_version, app_schema = ctx
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}
    viewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': viewer_id, 'role': 'viewer'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # viewer cannot create (edit required)
            r = c.post("/api/v1/agents", json={
                "name": "X", "default_model_config_version_id": model_version,
                "default_model_name": "gpt-4o", "application_state_schema_version_id": app_schema,
            }, headers=viewer_headers)
            assert r.status_code == 403
            # create (editor) with Idempotency-Key format validation
            r = c.post("/api/v1/agents", json={
                "name": "API Agent", "description": "d",
                "default_model_config_version_id": model_version, "default_model_name": "gpt-4o",
                "system_prompt": "p", "memory_settings": {},
                "application_state_schema_version_id": app_schema,
            }, headers={**editor_headers, "Idempotency-Key": "ag-create-1234567890"})
            assert r.status_code == 201
            agent = r.json()["data"]
            assert agent["version_no"] == 1
            assert len(agent["config_hash"]) == 64
            agent_id = agent["agent_id"]
            # invalid idempotency key format -> 422
            r = c.post("/api/v1/agents", json={
                "name": "X", "default_model_config_version_id": model_version,
                "default_model_name": "gpt-4o", "application_state_schema_version_id": app_schema,
            }, headers={**editor_headers, "Idempotency-Key": "short"})
            assert r.status_code == 422
            # detail + versions (view_config) — viewer with no grant gets existence-hiding 404
            assert c.get(f"/api/v1/agents/{agent_id}", headers=viewer_headers).status_code == 404
            detail = c.get(f"/api/v1/agents/{agent_id}", headers=editor_headers)
            assert detail.status_code == 200
            assert detail.json()["data"]["name"] == "API Agent"
            # version N+1 via Basic save
            r = c.post(f"/api/v1/agents/{agent_id}/versions", json={
                "base_version_no": 1, "name": "API Agent v2", "description": "d2",
                "default_model_config_version_id": model_version, "default_model_name": "gpt-4o",
                "system_prompt": "p2", "memory_settings": {},
                "application_state_schema_version_id": app_schema,
            }, headers={**editor_headers, "Idempotency-Key": "ag-version-1234567890"})
            assert r.status_code == 201
            assert r.json()["data"]["version_no"] == 2
            versions = c.get(f"/api/v1/agents/{agent_id}/versions", headers=editor_headers).json()["data"]
            assert [v["version_no"] for v in versions["items"]] == [1, 2]
            # stale base -> 409
            r = c.post(f"/api/v1/agents/{agent_id}/versions", json={
                "base_version_no": 1, "name": "X", "default_model_config_version_id": model_version,
                "default_model_name": "gpt-4o", "system_prompt": "p", "memory_settings": {},
                "application_state_schema_version_id": app_schema,
            }, headers={**editor_headers, "Idempotency-Key": "ag-version-1234567891"})
            assert r.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_agent_catalog_filters(ctx):
    from fastapi.testclient import TestClient

    session, editor_id, viewer_id, model_version, app_schema = ctx
    _create_agent(session, editor_id, model_version, app_schema)
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            models = c.get("/api/v1/agents/catalog/models", headers=editor_headers)
            assert models.status_code == 200
            items = models.json()["data"]["items"]
            names = {m["name"] for m in items}
            assert "m" in names          # active identity present
            assert "blocked" not in names  # migration_blocked excluded
            assert all("api_key" not in m for m in items)
    finally:
        app.dependency_overrides.clear()


def test_agent_catalog_model_id_is_a_version_id(ctx):
    """Regression (Issue 1): the catalog `id` must be a model_config_versions.id
    (the active version), not the model_configs identity — create/save pin it as
    default_model_config_version_id, which the runtime resolves by version id."""
    from fastapi.testclient import TestClient
    from app.services.agent.configuration import create_agent

    session, editor_id, viewer_id, model_version, app_schema = ctx
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            items = c.get("/api/v1/agents/catalog/models", headers=editor_headers).json()["data"]["items"]
            assert items, "catalog must expose at least one model"
            for item in items:
                row = session.execute(text(
                    "SELECT v.id, v.model_config_id, mc.active_version_id "
                    "FROM model_config_versions v JOIN model_configs mc ON mc.id = v.model_config_id "
                    "WHERE v.id = :id"
                ), {"id": item["id"]}).mappings().one_or_none()
                assert row is not None, f"catalog id {item['id']} is not a model_config_versions.id"
                assert row["active_version_id"] == item["id"], \
                    f"catalog id {item['id']} is not the active version of its identity"
            # a catalog id must be directly usable by the create transaction
            catalog_id = items[0]["id"]
            result = create_agent(
                session, actor_id=editor_id, name="Catalog Agent", description="d",
                default_model_config_version_id=catalog_id, default_model_name=items[0]["name"],
                system_prompt="p", memory_settings={},
                application_state_schema_version_id=app_schema,
            )
            assert result["version_no"] == 1
            assert session.execute(text(
                "SELECT default_model_config_version_id FROM agent_versions WHERE id = :id"
            ), {"id": result["version_id"]}).scalar_one() == catalog_id
    finally:
        app.dependency_overrides.clear()


def test_agent_list_contract_and_archive(ctx):
    """I-5: cursor pagination (limit 1-100 default 50), q/id/name/UTC date
    filters, stable created_at DESC, id DESC ordering, and DELETE archive 204."""
    from fastapi.testclient import TestClient

    session, editor_id, viewer_id, model_version, app_schema = ctx
    _create_agent(session, editor_id, model_version, app_schema, name="Alpha Agent")
    _create_agent(session, editor_id, model_version, app_schema, name="Beta Agent")
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}
    viewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': viewer_id, 'role': 'viewer'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # viewer without a grant sees nothing (access-filtered)
            assert c.get("/api/v1/agents", headers=viewer_headers).json()["data"]["items"] == []
            # default limit 50, has_more false, stable order by created_at DESC
            page = c.get("/api/v1/agents", headers=editor_headers).json()["data"]
            assert len(page["items"]) == 2
            assert page["has_more"] is False
            assert page["next_cursor"] is None
            assert [a["name"] for a in page["items"]] == ["Beta Agent", "Alpha Agent"]
            assert all(a["can_edit"] is True for a in page["items"])
            # limit=1 cursor pagination: first page has_more, second page is the rest
            first = c.get("/api/v1/agents", params={"limit": 1}, headers=editor_headers).json()["data"]
            assert len(first["items"]) == 1
            assert first["has_more"] is True
            assert first["next_cursor"]
            second = c.get("/api/v1/agents", params={"limit": 1, "cursor": first["next_cursor"]}, headers=editor_headers).json()["data"]
            assert len(second["items"]) == 1
            assert second["has_more"] is False
            assert {first["items"][0]["agent_id"], second["items"][0]["agent_id"]} == \
                {a["agent_id"] for a in page["items"]}
            # limit bounds
            assert c.get("/api/v1/agents", params={"limit": 0}, headers=editor_headers).status_code == 422
            assert c.get("/api/v1/agents", params={"limit": 101}, headers=editor_headers).status_code == 422
            # tampered cursor rejected
            assert c.get("/api/v1/agents", params={"cursor": "forged"}, headers=editor_headers).status_code == 422
            # name filter
            named = c.get("/api/v1/agents", params={"name": "alpha"}, headers=editor_headers).json()["data"]
            assert [a["name"] for a in named["items"]] == ["Alpha Agent"]
            # id exact-UUID-or-prefix filter
            beta_id = next(a["agent_id"] for a in page["items"] if a["name"] == "Beta Agent")
            by_id = c.get("/api/v1/agents", params={"id": beta_id}, headers=editor_headers).json()["data"]
            assert [a["agent_id"] for a in by_id["items"]] == [beta_id]
            by_prefix = c.get("/api/v1/agents", params={"id": beta_id[:8]}, headers=editor_headers).json()["data"]
            assert [a["agent_id"] for a in by_prefix["items"]] == [beta_id]
            # q search matches name substring or id prefix
            q_hit = c.get("/api/v1/agents", params={"q": "beta"}, headers=editor_headers).json()["data"]
            assert [a["name"] for a in q_hit["items"]] == ["Beta Agent"]
            # date filters: [created_from, created_before) with a future from excludes everything
            future = "2999-01-01T00:00:00Z"
            assert c.get("/api/v1/agents", params={"created_from": future}, headers=editor_headers).json()["data"]["items"] == []
            # DELETE archive -> 204 with no body; second delete -> 404
            r = c.delete(f"/api/v1/agents/{beta_id}", headers=editor_headers)
            assert r.status_code == 204
            assert r.content == b""
            detail = c.get(f"/api/v1/agents/{beta_id}", headers=editor_headers).json()["data"]
            assert detail["status"] == "archived"
            assert c.delete(f"/api/v1/agents/{beta_id}", headers=editor_headers).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_prompt_generation_routes(ctx):
    """§12 prompt-generation HTTP contract: POST 202 (sanitized receipt +
    output_text), GET 200 detail, POST decision 200 accept/reject, double
    decision -> 409 PROMPT_GENERATION_ALREADY_RESOLVED, Agent-edit auth with
    existence-hiding 404, Idempotency-Key format validation on writes."""
    from fastapi.testclient import TestClient

    session, editor_id, viewer_id, model_version, app_schema = ctx
    agent = _create_agent(session, editor_id, model_version, app_schema, name="Prompt Agent")
    agent_id = agent["agent_id"]
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}
    viewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': viewer_id, 'role': 'viewer'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # viewer role cannot generate (403 role ceiling)
            assert c.post(
                f"/api/v1/agents/{agent_id}/prompt-generations",
                json={"base_version_no": 1, "model_config_version_id": model_version,
                      "model_name": "gpt-4o", "input_text": "Draft <script>alert(1)</script>"},
                headers=viewer_headers,
            ).status_code == 403
            # an editor without the edit grant gets existence-hiding 404
            stranger_id = str(uuid.uuid4())
            session.execute(text(
                "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
                "VALUES (:id,'stranger','st@t.com','h','editor',true,:d,now(),now())"
            ), {"id": stranger_id, "d": DEFAULT_DOMAIN})
            session.commit()
            stranger_headers = {"Authorization": f"Bearer {create_access_token({'sub': stranger_id, 'role': 'editor'})}"}
            assert c.post(
                f"/api/v1/agents/{agent_id}/prompt-generations",
                json={"base_version_no": 1, "model_config_version_id": model_version,
                      "model_name": "gpt-4o", "input_text": "Draft"},
                headers={**stranger_headers, "Idempotency-Key": "ag-prompt-stranger-123456"},
            ).status_code == 404
            # invalid idempotency key -> 422
            r = c.post(
                f"/api/v1/agents/{agent_id}/prompt-generations",
                json={"base_version_no": 1, "model_config_version_id": model_version,
                      "model_name": "gpt-4o", "input_text": "Draft"},
                headers={**editor_headers, "Idempotency-Key": "short"},
            )
            assert r.status_code == 422
            # generate -> 202, sanitized receipt with output_text and hashes
            r = c.post(
                f"/api/v1/agents/{agent_id}/prompt-generations",
                json={"base_version_no": 1, "model_config_version_id": model_version,
                      "model_name": "gpt-4o", "input_text": "Draft <script>alert(1)</script>"},
                headers={**editor_headers, "Idempotency-Key": "ag-prompt-1234567890"},
            )
            assert r.status_code == 202
            data = r.json()["data"]
            assert data["status"] == "pending"
            assert data["agent_id"] == agent_id
            assert len(data["input_hash"]) == 64
            assert data["input_hash"] == data["output_hash"]
            assert "<script>" not in data["output_text"]
            assert data["output_text"] == "Draft alert(1)"
            generation_id = data["id"]
            # detail -> 200 (Agent edit auth)
            detail = c.get(f"/api/v1/agents/{agent_id}/prompt-generations/{generation_id}", headers=editor_headers)
            assert detail.status_code == 200
            assert detail.json()["data"]["id"] == generation_id
            # stranger editor detail -> existence-hiding 404
            assert c.get(f"/api/v1/agents/{agent_id}/prompt-generations/{generation_id}", headers=stranger_headers).status_code == 404
            # accept -> 200
            r = c.post(
                f"/api/v1/agents/{agent_id}/prompt-generations/{generation_id}/decision",
                json={"decision": "accepted"},
                headers={**editor_headers, "Idempotency-Key": "ag-prompt-dec-1234567890"},
            )
            assert r.status_code == 200
            assert r.json()["data"]["status"] == "accepted"
            # double decision -> 409 PROMPT_GENERATION_ALREADY_RESOLVED
            r = c.post(
                f"/api/v1/agents/{agent_id}/prompt-generations/{generation_id}/decision",
                json={"decision": "rejected"},
                headers={**editor_headers, "Idempotency-Key": "ag-prompt-dec-1234567891"},
            )
            assert r.status_code == 409
            assert "PROMPT_GENERATION_ALREADY_RESOLVED" in r.json()["detail"]
            # reject a fresh generation -> 200 rejected
            r = c.post(
                f"/api/v1/agents/{agent_id}/prompt-generations",
                json={"base_version_no": 1, "model_config_version_id": model_version,
                      "model_name": "gpt-4o", "input_text": "Another"},
                headers={**editor_headers, "Idempotency-Key": "ag-prompt-1234567892"},
            )
            gen2 = r.json()["data"]["id"]
            r = c.post(
                f"/api/v1/agents/{agent_id}/prompt-generations/{gen2}/decision",
                json={"decision": "rejected"},
                headers={**editor_headers, "Idempotency-Key": "ag-prompt-dec-1234567893"},
            )
            assert r.status_code == 200
            assert r.json()["data"]["status"] == "rejected"
            # unknown generation -> 404
            assert c.get(f"/api/v1/agents/{agent_id}/prompt-generations/nope", headers=editor_headers).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_tool_validation_route(ctx):
    """§12 catalog/config validation: `POST /agents/{id}/tool-validation`
    validates discoverability (OntologyProjectAccessGrant.discover) and the
    data-capability intersection via the P2B-POLICY path (the principal's own
    ontology_data_grants ∩ role ceiling — fail closed); Agent edit auth with
    existence-hiding 404.  Read-only POST, so no Idempotency-Key required."""
    from fastapi.testclient import TestClient

    session, editor_id, viewer_id, model_version, app_schema = ctx
    agent = _create_agent(session, editor_id, model_version, app_schema, name="Tool Agent")
    agent_id = agent["agent_id"]
    # seed a published ontology + project discover grant + data grant for the editor
    session.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-tool','Tool Ontology','test','v1','published',:u,now(),now(),:d,1)"
    ), {"u": editor_id, "d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO ontology_project_access_grants (id, ontology_id, user_id, security_domain_id, capabilities, status, revision, created_by, created_at, updated_at) "
        "VALUES (:id, 'o-tool', :u, :d, CAST(:caps AS jsonb), 'active', 1, :u, now(), now())"
    ), {"id": str(uuid.uuid4()), "u": editor_id, "d": DEFAULT_DOMAIN, "caps": '["discover", "read", "edit", "publish"]'})
    session.execute(text(
        "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, policy_version, status, revision, created_by, created_at, updated_at) "
        "VALUES (:id, 'o-tool', :u, CAST(:caps AS jsonb), 'restricted-policy-dsl-v1', 'active', 1, :u, now(), now())"
    ), {"id": str(uuid.uuid4()), "u": editor_id,
        "caps": '["read_schema", "read_instances", "traverse_relations", "execute_read_logic"]'})
    session.commit()
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}
    viewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': viewer_id, 'role': 'viewer'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # viewer -> 403 role ceiling
            assert c.post(
                f"/api/v1/agents/{agent_id}/tool-validation",
                json={"ontology_ids": ["o-tool"]},
                headers=viewer_headers,
            ).status_code == 403
            # valid binding -> 200 valid with capability intersection (no Idempotency-Key)
            r = c.post(
                f"/api/v1/agents/{agent_id}/tool-validation",
                json={"ontology_ids": ["o-tool"]},
                headers=editor_headers,
            )
            assert r.status_code == 200
            data = r.json()["data"]
            assert data["valid"] is True
            assert data["blocked"] == []
            assert "read_schema" in data["capabilities"]
            assert "read_instances" in data["capabilities"]
            # undiscoverable ontology -> blocked, not valid
            r = c.post(
                f"/api/v1/agents/{agent_id}/tool-validation",
                json={"ontology_ids": ["o-ghost"]},
                headers=editor_headers,
            )
            assert r.status_code == 200
            data = r.json()["data"]
            assert data["valid"] is False
            assert data["blocked"] == ["o-ghost"]
            # discoverable but no data grant -> blocked (fail closed)
            session.execute(text(
                "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
                "VALUES ('o-nodata','No Data Ontology','test','v1','published',:u,now(),now(),:d,1)"
            ), {"u": editor_id, "d": DEFAULT_DOMAIN})
            session.execute(text(
                "INSERT INTO ontology_project_access_grants (id, ontology_id, user_id, security_domain_id, capabilities, status, revision, created_by, created_at, updated_at) "
                "VALUES (:id, 'o-nodata', :u, :d, CAST(:caps AS jsonb), 'active', 1, :u, now(), now())"
            ), {"id": str(uuid.uuid4()), "u": editor_id, "d": DEFAULT_DOMAIN, "caps": '["discover", "read"]'})
            session.commit()
            r = c.post(
                f"/api/v1/agents/{agent_id}/tool-validation",
                json={"ontology_ids": ["o-nodata"]},
                headers=editor_headers,
            )
            assert r.status_code == 200
            data = r.json()["data"]
            assert data["valid"] is False
            assert data["blocked"] == ["o-nodata"]
    finally:
        app.dependency_overrides.clear()


def test_agent_version_detail_and_restore_routes(ctx):
    """§12 version detail + restore (201, N+1 from the pinned version)."""
    from fastapi.testclient import TestClient

    session, editor_id, viewer_id, model_version, app_schema = ctx
    agent = _create_agent(session, editor_id, model_version, app_schema, name="Version Agent")
    agent_id = agent["agent_id"]
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}
    viewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': viewer_id, 'role': 'viewer'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # detail: viewer without grant -> existence-hiding 404
            assert c.get(f"/api/v1/agents/{agent_id}/versions/1", headers=viewer_headers).status_code == 404
            r = c.get(f"/api/v1/agents/{agent_id}/versions/1", headers=editor_headers)
            assert r.status_code == 200
            assert r.json()["data"]["version_no"] == 1
            assert r.json()["data"]["name"] == "Version Agent"
            # unknown version -> 404
            assert c.get(f"/api/v1/agents/{agent_id}/versions/99", headers=editor_headers).status_code == 404
            # restore: missing/invalid Idempotency-Key -> 422
            r = c.post(f"/api/v1/agents/{agent_id}/versions/1/restore",
                       json={"change_note": "restore"}, headers=editor_headers)
            assert r.status_code == 422
            # restore the pinned v1 -> 201, N+1 (v2), same config hash
            r = c.post(f"/api/v1/agents/{agent_id}/versions/1/restore",
                       json={"change_note": "restore v1"},
                       headers={**editor_headers, "Idempotency-Key": "ag-restore-1234567890"})
            assert r.status_code == 201, r.text
            data = r.json()["data"]
            assert data["version_no"] == 2
            assert data["config_hash"] == agent["config_hash"]
            # unknown pinned version -> 422 VERSION_NOT_FOUND
            r = c.post(f"/api/v1/agents/{agent_id}/versions/99/restore",
                       json={}, headers={**editor_headers, "Idempotency-Key": "ag-restore-1234567891"})
            assert r.status_code == 422
            assert "VERSION_NOT_FOUND" in r.text
    finally:
        app.dependency_overrides.clear()


def test_agent_access_grants_routes(ctx):
    """§12 agent access grants: owner invariant, no self-escalation, CAS."""
    from fastapi.testclient import TestClient

    session, editor_id, viewer_id, model_version, app_schema = ctx
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-grantee','grantee','g@t.com','h','viewer',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    session.commit()
    agent = _create_agent(session, editor_id, model_version, app_schema, name="Grant Agent")
    agent_id = agent["agent_id"]
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}
    viewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': viewer_id, 'role': 'viewer'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # viewer cannot list/create (role ceiling 403)
            assert c.get(f"/api/v1/agents/{agent_id}/access-grants", headers=viewer_headers).status_code == 403
            # owner invariant: granting the owner is 422
            r = c.post(f"/api/v1/agents/{agent_id}/access-grants",
                       json={"user_id": editor_id, "capabilities": ["run"]},
                       headers={**editor_headers, "Idempotency-Key": "ag-grant-owner-00000001"})
            assert r.status_code == 422 and "OWNER_INVARIANT" in r.text
            # create a grant for the grantee -> 201
            r = c.post(f"/api/v1/agents/{agent_id}/access-grants",
                       json={"user_id": "u-grantee", "capabilities": ["view_config"]},
                       headers={**editor_headers, "Idempotency-Key": "ag-grant-create-0000001"})
            assert r.status_code == 201, r.text
            grant = r.json()["data"]
            grant_id = grant["id"]
            assert grant["revision"] == 1
            # list -> 200
            r = c.get(f"/api/v1/agents/{agent_id}/access-grants", headers=editor_headers)
            assert r.status_code == 200
            assert "u-grantee" in [g["user_id"] for g in r.json()["data"]["items"]]
            # CAS revise -> 201
            r = c.post(f"/api/v1/agents/{agent_id}/access-grants/{grant_id}/revisions",
                       json={"base_revision": 1, "capabilities": ["view_config", "run"]},
                       headers={**editor_headers, "Idempotency-Key": "ag-grant-revise-0000001"})
            assert r.status_code == 201, r.text
            assert set(r.json()["data"]["capabilities"]) == {"view_config", "run"}
            # stale revision -> 409
            r = c.post(f"/api/v1/agents/{agent_id}/access-grants/{grant_id}/revisions",
                       json={"base_revision": 1, "capabilities": ["run"]},
                       headers={**editor_headers, "Idempotency-Key": "ag-grant-revise-0000002"})
            assert r.status_code == 409 and "AGENT_GRANT_CONFLICT" in r.text
            # CAS revoke -> 200
            r = c.post(f"/api/v1/agents/{agent_id}/access-grants/{grant_id}/revoke",
                       json={"base_revision": 2},
                       headers={**editor_headers, "Idempotency-Key": "ag-grant-revoke-0000001"})
            assert r.status_code == 200, r.text
            assert r.json()["data"]["status"] == "revoked"
            # revoked grant is gone from the active list
            r = c.get(f"/api/v1/agents/{agent_id}/access-grants", headers=editor_headers)
            assert "u-grantee" not in [g["user_id"] for g in r.json()["data"]["items"]]
    finally:
        app.dependency_overrides.clear()


def test_reconciliation_detail_route(ctx):
    """§12 reconciliation detail: `GET /admin/agent-reconciliations/{id}`."""
    from fastapi.testclient import TestClient

    session, editor_id, viewer_id, model_version, app_schema = ctx
    # the ctx schema is at 0005; agent_reconciliation_cases is a 0006 table —
    # create it via the runtime migration for this fixture's schema
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # editor (non-admin) -> 403
            assert c.get("/api/v1/admin/agent-reconciliations/none", headers=editor_headers).status_code == 403
    finally:
        app.dependency_overrides.clear()


def _seed_published_tool_ontology(session, editor_id, ontology_id="o-tools", with_logic=True):
    """Published ontology with one enabled Logic rule + one enabled Action +
    project grant + data grant (P2B-TOOLS fixture)."""
    session.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES (:o,'Tool Ontology','test','v1','created',:u,now(),now(),:d,1)"
    ), {"o": ontology_id, "u": editor_id, "d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO ontology_project_access_grants (id, ontology_id, user_id, security_domain_id, capabilities, status, revision, created_by, created_at, updated_at) "
        "VALUES (:id, :o, :u, :d, CAST(:caps AS jsonb), 'active', 1, :u, now(), now())"
    ), {"id": str(uuid.uuid4()), "o": ontology_id, "u": editor_id, "d": DEFAULT_DOMAIN,
        "caps": '["discover", "read", "edit", "publish"]'})
    session.execute(text(
        "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, policy_version, status, revision, created_by, created_at, updated_at) "
        "VALUES (:id, :o, :u, CAST(:caps AS jsonb), 'restricted-policy-dsl-v1', 'active', 1, :u, now(), now())"
    ), {"id": str(uuid.uuid4()), "o": ontology_id, "u": editor_id,
        "caps": '["read_schema", "read_instances", "traverse_relations", "execute_read_logic", "execute_instance_action"]'})
    if with_logic:
        session.execute(text(
            "INSERT INTO v2_ontology_logic_rules (id, ontology_id, name, logic_type, description, target_entity_type, expression, severity, enabled, status, version, created_at, updated_at) "
            "VALUES (:id, :o, 'Rule: completeness', 'validation', 'd', 'Order', CAST(:expr AS json), 'warning', true, 'draft', 1, now(), now())"
        ), {"id": "rule-1", "o": ontology_id, "expr": '{"column": "table_index"}'})
    session.execute(text(
        "INSERT INTO v2_ontology_action_types (id, ontology_id, name, description, target_entity_type, action_category, parameters, effects, enabled, status, version, created_at, updated_at) "
        "VALUES (:id, :o, 'Create Order', 'create', 'Order', 'crud', CAST(:params AS json), CAST(:effects AS json), true, 'draft', 1, now(), now())"
    ), {"id": "action-1", "o": ontology_id,
        "params": '[{"name": "data", "type": "object", "required": true}]',
        "effects": '[{"action": "create_object", "entity_type": "Order"}]'})
    session.commit()
    from app.services.publication.lifecycle import publish
    publish(db=session, ontology_id=ontology_id, actor_id=editor_id, changelog="v1")


def test_ontology_tools_exposure_endpoint(ctx):
    """P2B-TOOLS exposure: `GET /api/v1/ontologies/{id}/tools` returns the
    published tool descriptors (built-in query + Logic + Action) from the
    latest release manifest, and `validate_binding_tools` accepts them."""
    from fastapi.testclient import TestClient

    session, editor_id, viewer_id, model_version, app_schema = ctx
    _seed_published_tool_ontology(session, editor_id)
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}
    viewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': viewer_id, 'role': 'viewer'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # viewer without a project grant -> existence-hiding 404
            assert c.get("/api/v1/ontologies/o-tools/tools", headers=viewer_headers).status_code == 404
            r = c.get("/api/v1/ontologies/o-tools/tools", headers=editor_headers)
            assert r.status_code == 200, r.text
            data = r.json()["data"]
            assert data["published"] is True
            assert data["release_id"]
            kinds = {t["source_kind"] for t in data["tools"]}
            assert "builtin" in kinds
            assert "logic" in kinds
            assert "action" in kinds
            ids = {t["descriptor_id"] for t in data["tools"]}
            assert "query:o-tools" in ids
            assert "logic:rule-1" in ids
            assert "action:action-1" in ids
            # a release manifest carries the same descriptors
            release_id = data["release_id"]
            rel = c.get(f"/api/v1/ontologies/o-tools/releases/{release_id}", headers=editor_headers)
            assert rel.status_code == 200
            projection = rel.json()["data"]["manifest_projection"]
            assert {d["descriptor_id"] for d in projection["tool_descriptors"]} == ids
            assert len(projection["logic_rules"]) == 1
            assert len(projection["actions"]) == 1
    finally:
        app.dependency_overrides.clear()


def test_agent_tool_selection_persistence(ctx):
    """P2B-TOOLS selection: createAgentVersion persists per-binding enabled
    tools (query + Logic + Action descriptor ids) in the immutable version
    tree; the detail DTO exposes them; a later save bumps N+1 and changes the
    config hash while the old version stays byte-identical."""
    from fastapi.testclient import TestClient

    session, editor_id, viewer_id, model_version, app_schema = ctx
    _seed_published_tool_ontology(session, editor_id)
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # create with bindings
            r = c.post("/api/v1/agents", json={
                "name": "Tool Select Agent", "description": "d",
                "default_model_config_version_id": model_version, "default_model_name": "gpt-4o",
                "system_prompt": "p", "memory_settings": {},
                "application_state_schema_version_id": app_schema,
                "ontology_bindings": [{
                    "ontology_id": "o-tools",
                    "capabilities": ["read_schema", "read_instances", "traverse_relations"],
                    "allowlists": {},
                    "selected_tools": ["query:o-tools", "logic:rule-1"],
                }],
            }, headers={**editor_headers, "Idempotency-Key": "ag-tool-create-0000001"})
            assert r.status_code == 201, r.text
            agent_id = r.json()["data"]["agent_id"]
            v1 = c.get(f"/api/v1/agents/{agent_id}/versions/1", headers=editor_headers).json()["data"]
            assert v1["ontology_bindings"] == [{
                "ontology_id": "o-tools",
                "capabilities": ["read_schema", "read_instances", "traverse_relations"],
                "allowlists": {},
                "selected_tools": ["query:o-tools", "logic:rule-1"],
            }]
            # selecting an unknown tool is rejected
            r = c.post(f"/api/v1/agents/{agent_id}/versions", json={
                "base_version_no": 1, "name": "Tool Select Agent", "description": "d",
                "default_model_config_version_id": model_version, "default_model_name": "gpt-4o",
                "system_prompt": "p", "memory_settings": {},
                "application_state_schema_version_id": app_schema,
                "ontology_bindings": [{
                    "ontology_id": "o-tools",
                    "capabilities": ["read_schema", "read_instances", "traverse_relations"],
                    "selected_tools": ["action:does-not-exist"],
                }],
            }, headers={**editor_headers, "Idempotency-Key": "ag-tool-save-00000001"})
            assert r.status_code == 422
            assert "AGENTS_TOOLS_SELECTION_INVALID" in r.text
            # save v2 with the full tool set selected
            r = c.post(f"/api/v1/agents/{agent_id}/versions", json={
                "base_version_no": 1, "name": "Tool Select Agent v2", "description": "d",
                "default_model_config_version_id": model_version, "default_model_name": "gpt-4o",
                "system_prompt": "p", "memory_settings": {},
                "application_state_schema_version_id": app_schema,
                "ontology_bindings": [{
                    "ontology_id": "o-tools",
                    "capabilities": ["read_schema", "read_instances", "traverse_relations",
                                     "execute_read_logic", "execute_instance_action"],
                    "selected_tools": ["query:o-tools", "logic:rule-1", "action:action-1"],
                }],
            }, headers={**editor_headers, "Idempotency-Key": "ag-tool-save-00000002"})
            assert r.status_code == 201, r.text
            v2no = r.json()["data"]["version_no"]
            assert v2no == 2
            v2 = c.get(f"/api/v1/agents/{agent_id}/versions/2", headers=editor_headers).json()["data"]
            assert sorted(v2["ontology_bindings"][0]["selected_tools"]) == ["action:action-1", "logic:rule-1", "query:o-tools"]
            # old version unchanged, hashes differ
            v1_again = c.get(f"/api/v1/agents/{agent_id}/versions/1", headers=editor_headers).json()["data"]
            assert v1_again["ontology_bindings"][0]["selected_tools"] == ["query:o-tools", "logic:rule-1"]
            assert v1_again["config_hash"] == v1["config_hash"]
            assert v2["config_hash"] != v1["config_hash"]
            # version list also exposes the active version's bindings
            listed = c.get(f"/api/v1/agents/{agent_id}/versions", headers=editor_headers).json()["data"]["items"]
            active = next(v for v in listed if v["version_no"] == 2)
            assert sorted(active["ontology_bindings"][0]["selected_tools"]) == \
                ["action:action-1", "logic:rule-1", "query:o-tools"]
    finally:
        app.dependency_overrides.clear()


def test_runtime_context_carries_tool_selection(ctx):
    """P2B-TOOLS runtime filtering: resolve_pinned_context + assemble_turn_context
    expose only the selected tool descriptors for the Agent's active version."""
    from app.runtime.langgraph_adapter import assemble_turn_context
    from app.services.runtime.context import resolve_pinned_context
    from app.services.runtime.turns import create_session, create_turn
    from app.services.agent.configuration import create_agent

    session, editor_id, viewer_id, model_version, app_schema = ctx
    _seed_published_tool_ontology(session, editor_id)
    agent = create_agent(
        session, actor_id=editor_id, name="Runtime Agent", description="d",
        default_model_config_version_id=model_version, default_model_name="gpt-4o",
        system_prompt="p", memory_settings={},
        application_state_schema_version_id=app_schema,
        ontology_bindings=[{
            "ontology_id": "o-tools",
            "capabilities": ["read_schema", "read_instances", "traverse_relations"],
            "allowlists": {},
            "selected_tools": ["query:o-tools", "logic:rule-1"],
        }],
    )
    session_id = create_session(session, agent_id=agent["agent_id"], actor_id=editor_id)["id"]
    turn = create_turn(session, session_id=session_id, user_message="test", actor_id=editor_id)
    pinned = resolve_pinned_context(session, turn_id=turn["turn_id"], session_id=session_id)
    assert pinned.ontology_tool_selection == ({
        "ontology_id": "o-tools",
        "capabilities": ["read_schema", "read_instances", "traverse_relations"],
        "selected_tools": ["query:o-tools", "logic:rule-1"],
    },)
    ctx_t = assemble_turn_context(
        turn_id=turn["turn_id"], session_id=session_id, agent_id=agent["agent_id"],
        agent_version_id=agent["version_id"], user_message="test",
        model_config_version_id=model_version, model_name="gpt-4o",
        ontology_bindings=[dict(b) for b in pinned.ontology_tool_selection],
    )
    assert ctx_t.extra["ontology_tool_selection"][0]["selected_tools"] == ["query:o-tools", "logic:rule-1"]
