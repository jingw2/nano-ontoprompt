"""P6A: governed retention policy/hold administration API."""
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
from app.services.auth_service import create_access_token

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


def _client(session):
    from app.deps import get_db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def ctx():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p6a_api_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0011_retention_governance").returncode == 0
    session = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    admin_id = str(uuid.uuid4())
    editor_id = str(uuid.uuid4())
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:id,'ret-admin','ra@t.com','h','admin',true,:d,now(),now())"
    ), {"id": admin_id, "d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:id,'ret-editor','re@t.com','h','editor',true,:d,now(),now())"
    ), {"id": editor_id, "d": DEFAULT_DOMAIN})
    session.commit()
    yield session, admin_id, editor_id
    session.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_p6a_retention_api_red_contract():
    router = BACKEND_DIR / "app" / "routers" / "retention.py"
    if not router.exists():
        pytest.fail("RED_P6A_RETENTION_API: missing app/routers/retention.py")


def test_create_and_activate_policy_via_api(ctx):
    from fastapi.testclient import TestClient
    session, admin_id, editor_id = ctx
    admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': admin_id, 'role': 'admin'})}"}
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}

    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # editor forbidden
            r = c.post("/api/v2/retention-policies",
                      json={"security_domain_id": DEFAULT_DOMAIN, "rules": {"message.redact": 180}},
                      headers=editor_headers)
            assert r.status_code == 403

            r = c.post("/api/v2/retention-policies",
                      json={"security_domain_id": DEFAULT_DOMAIN, "rules": {"message.redact": 180}},
                      headers=admin_headers)
            assert r.status_code == 201, r.text
            version_id = r.json()["data"]["id"]
            # migration 0011 backfills a built-in version_no=1 for the default
            # domain seeded by 0003, so the first API-created version is 2
            assert r.json()["data"]["version_no"] == 2

            r = c.post("/api/v2/retention-policies/" + version_id + "/activate",
                      json={"security_domain_id": DEFAULT_DOMAIN, "base_epoch": 0},
                      headers=admin_headers)
            assert r.status_code == 200, r.text
            assert r.json()["data"]["active_version_id"] == version_id
            assert r.json()["data"]["epoch"] == 1

            r = c.get("/api/v2/retention-policies", headers=admin_headers)
            assert r.status_code == 200
            assert len(r.json()["data"]["items"]) >= 1
    finally:
        app.dependency_overrides.clear()


def test_create_policy_below_minimum_returns_422(ctx):
    from fastapi.testclient import TestClient
    session, admin_id, _ = ctx
    admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': admin_id, 'role': 'admin'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            r = c.post("/api/v2/retention-policies",
                      json={"security_domain_id": DEFAULT_DOMAIN, "rules": {"message.redact": 1}},
                      headers=admin_headers)
            assert r.status_code == 422
            assert "RETENTION_MINIMUM_VIOLATION" in r.text
    finally:
        app.dependency_overrides.clear()


def test_create_and_release_hold_via_api(ctx):
    from fastapi.testclient import TestClient
    session, admin_id, _ = ctx
    admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': admin_id, 'role': 'admin'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            r = c.post("/api/v2/retention-holds",
                      json={"security_domain_id": DEFAULT_DOMAIN, "scope_type": "turn",
                            "scope_id": "t-1", "reason": "litigation"},
                      headers=admin_headers)
            assert r.status_code == 201, r.text
            hold_id = r.json()["data"]["id"]

            r = c.get("/api/v2/retention-holds", headers=admin_headers)
            assert r.status_code == 200
            assert any(h["id"] == hold_id for h in r.json()["data"]["items"])

            r = c.post(f"/api/v2/retention-holds/{hold_id}/release",
                      json={"security_domain_id": DEFAULT_DOMAIN}, headers=admin_headers)
            assert r.status_code == 200, r.text
            assert r.json()["data"]["released"] is True
    finally:
        app.dependency_overrides.clear()
