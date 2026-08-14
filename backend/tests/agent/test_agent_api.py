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
    assert _alembic(schema, "upgrade", "0005_agent_configuration").returncode == 0
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
