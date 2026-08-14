"""P3B-STATEADMIN: govern application state schemas.

Administrator-only registry/version/activate APIs with bounded JSON Schema
validation, canonical hashing and CAS; schema bombs and unknown revisions are
rejected; archive is refused while an AgentVersion references the schema.
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


def test_p3b_stateadmin_red_contract():
    failures = []
    router = BACKEND_DIR / "app" / "routers" / "application_state_schemas.py"
    if not router.exists():
        failures.append("missing app/routers/application_state_schemas.py")
    service = BACKEND_DIR / "app" / "services" / "application_state_schema.py"
    if not service.exists():
        failures.append("missing app/services/application_state_schema.py")
    else:
        source = service.read_text()
        for symbol in ("create_registry_with_version", "create_schema_version", "activate_schema_version", "validate_bounded_json_schema"):
            if symbol not in source:
                failures.append(f"application_state_schema.py missing {symbol}")
    if failures:
        pytest.fail("RED_P3B_STATEADMIN: " + "; ".join(failures))


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
    schema = "p3b_stateadmin_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0005_agent_configuration").returncode == 0
    Session = sessionmaker(bind=create_engine(_scoped_url(schema)))
    with Session() as session:
        admin_id = str(uuid.uuid4())
        editor_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES (:id,:u,:e,'h',:r,true,:d,now(),now())"
        ), {"id": admin_id, "u": "ass-admin", "e": "a@t.com", "r": "admin", "d": DEFAULT_DOMAIN})
        session.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES (:id,:u,:e,'h',:r,true,:d,now(),now())"
        ), {"id": editor_id, "u": "ass-editor", "e": "e@t.com", "r": "editor", "d": DEFAULT_DOMAIN})
        session.commit()
        yield session, admin_id, editor_id
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_schema_registry_lifecycle_cas_and_audit(ctx):
    from fastapi.testclient import TestClient
    from app.deps import get_db

    session, admin_id, editor_id = ctx
    admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': admin_id, 'role': 'admin'})}"}
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            # editor is denied (admin-only)
            r = client.post("/api/v2/application-state-schemas", json={
                "application_key": "chat-x", "json_schema": {"type": "object", "properties": {}},
            }, headers=editor_headers)
            assert r.status_code == 403
            # schema bomb rejected
            deep = {"type": "object", "properties": {}}
            node = deep
            for _ in range(30):
                child = {"type": "object", "properties": {}}
                node["properties"]["a"] = child
                node = child
            r = client.post("/api/v2/application-state-schemas", json={
                "application_key": "bomb", "json_schema": deep,
            }, headers=admin_headers)
            assert r.status_code == 422
            # create registry + version 1
            r = client.post("/api/v2/application-state-schemas", json={
                "application_key": "chat-x",
                "json_schema": {"type": "object", "properties": {"locale": {"type": "string"}}},
            }, headers=admin_headers)
            assert r.status_code == 201
            registry = r.json()["data"]
            assert registry["active_version"]["version_no"] == 1
            assert len(registry["active_version"]["canonical_hash"]) == 64
            # duplicate key rejected
            r = client.post("/api/v2/application-state-schemas", json={
                "application_key": "chat-x", "json_schema": {"type": "object", "properties": {}},
            }, headers=admin_headers)
            assert r.status_code == 409
            # version N+1 under CAS
            r = client.post(f"/api/v2/application-state-schemas/{registry['id']}/versions", json={
                "base_active_revision": 1,
                "json_schema": {"type": "object", "properties": {"locale": {"type": "string"}, "theme": {"type": "string"}}},
            }, headers=admin_headers)
            assert r.status_code == 201
            version2 = r.json()["data"]
            assert version2["version_no"] == 2
            assert version2["status"] == "inactive"
            # activate version 2 under CAS before the stale-base check
            r = client.post(f"/api/v2/application-state-schemas/{registry['id']}/activate", json={
                "base_active_revision": 1, "target_revision": version2["id"],
            }, headers=admin_headers)
            assert r.status_code == 200
            assert r.json()["data"]["active_version_no"] == 2
            # stale CAS -> 409 (active is now 2, request says 1)
            r = client.post(f"/api/v2/application-state-schemas/{registry['id']}/versions", json={
                "base_active_revision": 1, "json_schema": {"type": "object", "properties": {}},
            }, headers=admin_headers)
            assert r.status_code == 409
            # archive via target_revision=null (active is 2)
            r = client.post(f"/api/v2/application-state-schemas/{registry['id']}/activate", json={
                "base_active_revision": 2, "target_revision": None,
            }, headers=admin_headers)
            assert r.status_code == 200
            assert r.json()["data"]["status"] == "archived"
            # audit rows recorded
            audit = session.execute(text(
                "SELECT count(*) FROM governance_audit_outbox WHERE correlation_id LIKE 'ass:%'"
            )).scalar_one()
            assert audit >= 4
    finally:
        app.dependency_overrides.clear()


def test_archive_refused_while_agent_version_references_schema(ctx):
    from fastapi.testclient import TestClient
    from app.deps import get_db

    session, admin_id, editor_id = ctx
    admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': admin_id, 'role': 'admin'})}"}

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            r = client.post("/api/v2/application-state-schemas", json={
                "application_key": "chat-ref",
                "json_schema": {"type": "object", "properties": {"locale": {"type": "string"}}},
            }, headers=admin_headers)
            registry = r.json()["data"]
            # seed a model config + immutable version for the AgentVersion FK
            session.execute(text(
                "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at) "
                "VALUES (:id,'m','llm',NULL,'','openai','[]'::json,'{}'::json,:owner,now(),now())"
            ), {"id": str(uuid.uuid4()), "owner": admin_id})
            model_id = session.execute(text("SELECT id FROM model_configs LIMIT 1")).scalar_one()
            model_version = str(uuid.uuid4())
            session.execute(text(
                "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_at) "
                "VALUES (:id, :mc, 1, 'openai', '{}'::json, :hash, '[]'::json, now())"
            ), {"id": model_version, "mc": model_id, "hash": "0" * 64})
            session.commit()
            session.execute(text(
                "INSERT INTO agents (id, visibility, status, owner_id, active_version_id, created_at, updated_at) "
                "VALUES (:id, 'private', 'active', :owner, NULL, now(), now())"
            ), {"id": str(uuid.uuid4()), "owner": admin_id})
            agent_id = session.execute(text("SELECT id FROM agents LIMIT 1")).scalar_one()
            session.execute(text(
                "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
                "default_model_name, memory_settings, application_state_schema_version_id, config_hash, created_by, created_at) "
                "VALUES (:id, :agent, 1, 'a', :mvid, 'm', '{}'::json, :svid, :hash, :owner, now())"
            ), {"id": str(uuid.uuid4()), "agent": agent_id, "mvid": model_version,
                "svid": registry["active_version"]["id"], "hash": "0" * 64, "owner": admin_id})
            session.commit()
            r = client.post(f"/api/v2/application-state-schemas/{registry['id']}/activate", json={
                "base_active_revision": 1, "target_revision": None,
            }, headers=admin_headers)
            assert r.status_code == 422
            assert "REFERENCED" in r.text
    finally:
        app.dependency_overrides.clear()
