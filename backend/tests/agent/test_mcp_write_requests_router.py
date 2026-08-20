"""Task 3: HTTP contract for the human-facing (interactive-JWT) write-request
list/detail/approve/reject endpoints."""
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def _scoped_url(schema):
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args], cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=_scoped_url(schema)), capture_output=True, text=True, check=check,
    )


@pytest.fixture(scope="module")
def mcp_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "mcp_wr_router_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    result = _alembic(schema, "upgrade", "0017_mcp_write_requests")
    assert result.returncode == 0, result.stderr
    session_engine = create_engine(_scoped_url(schema))
    Session = sessionmaker(bind=session_engine)
    yield Session
    session_engine.dispose()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


@pytest.fixture
def pg_client(mcp_db):
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.main import app

    Session = mcp_db

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _add_user(Session, username, role="viewer"):
    from app.services.auth_service import hash_password
    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :username, :email, :pw, :role, true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "username": username, "email": f"{username}@example.com",
            "pw": hash_password("secret123"), "role": role, "domain": DEFAULT_DOMAIN})
        session.commit()
        return session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).scalar_one()


def _login(client, username):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "secret123"})
    assert response.status_code == 200, response.text
    return response.json()["data"]["access_token"]


def _add_client(Session, admin_id):
    from app.services import oauth_clients
    with Session() as session:
        client = oauth_clients.create_client(
            session, client_name="X", redirect_uris=[], allowed_scopes=["ontology:write"], created_by=admin_id,
        )
        return client.id


def _add_ontology_and_release(Session, created_by):
    with Session() as session:
        ontology_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO ontology_projects (id, name, domain, version, status, created_by, created_at, updated_at) "
            "VALUES (:id, 'p', 'd', 'v0.1', 'draft', :created_by, now(), now())"
        ), {"id": ontology_id, "created_by": created_by})
        release_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO ontology_releases (id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, created_by, created_at) "
            "VALUES (:id, :oid, 1, 'v1', :mb, '{}'::jsonb, digest(:mb,'sha256'), :uid, now())"
        ), {"id": release_id, "oid": ontology_id, "mb": b"{}", "uid": created_by})
        session.commit()
        return ontology_id, release_id


def _grant_write(Session, ontology_id, user_id, created_by):
    with Session() as session:
        session.execute(text(
            "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, status, created_at, revision, created_by) "
            "VALUES (:id, :o, :u, :cap, 'active', now(), 1, :created_by)"
        ), {"id": str(uuid.uuid4()), "o": ontology_id, "u": user_id, "cap": '["execute_instance_action"]', "created_by": created_by})
        session.commit()


def test_list_approve_reject_flow(pg_client, mcp_db):
    from app.services.mcp_write_requests import create_write_request

    admin_id = _add_user(mcp_db, "admin-" + uuid.uuid4().hex[:8], role="admin")
    owner_username = "owner-" + uuid.uuid4().hex[:8]
    owner_id = _add_user(mcp_db, owner_username)
    client_id = _add_client(mcp_db, admin_id)
    ontology_id, release_id = _add_ontology_and_release(mcp_db, admin_id)
    _grant_write(mcp_db, ontology_id, owner_id, admin_id)
    with mcp_db() as session:
        result = create_write_request(
            session, oauth_client_id=client_id, user_id=owner_id, ontology_id=ontology_id,
            release_id=release_id, descriptor_id="action:x", parameters={},
        )

    owner_token = _login(pg_client, owner_username)
    listed = pg_client.get("/api/v1/mcp/write-requests", headers={"Authorization": f"Bearer {owner_token}"})
    assert listed.status_code == 200
    assert len(listed.json()["data"]["items"]) == 1

    detail = pg_client.get(f"/api/v1/mcp/write-requests/{result['request_id']}", headers={"Authorization": f"Bearer {owner_token}"})
    assert detail.status_code == 200 and detail.json()["data"]["status"] == "pending"

    approved = pg_client.post(f"/api/v1/mcp/write-requests/{result['request_id']}/approve", headers={"Authorization": f"Bearer {owner_token}"})
    assert approved.status_code == 200 and approved.json()["data"]["status"] == "approved"

    # already resolved -> 404
    again = pg_client.post(f"/api/v1/mcp/write-requests/{result['request_id']}/reject", headers={"Authorization": f"Bearer {owner_token}"})
    assert again.status_code == 404


def test_other_user_cannot_see_or_resolve_someone_elses_request(pg_client, mcp_db):
    from app.services.mcp_write_requests import create_write_request

    admin_id = _add_user(mcp_db, "admin-" + uuid.uuid4().hex[:8], role="admin")
    owner_id = _add_user(mcp_db, "owner-" + uuid.uuid4().hex[:8])
    other_username = "other-" + uuid.uuid4().hex[:8]
    _add_user(mcp_db, other_username)
    client_id = _add_client(mcp_db, admin_id)
    ontology_id, release_id = _add_ontology_and_release(mcp_db, admin_id)
    _grant_write(mcp_db, ontology_id, owner_id, admin_id)
    with mcp_db() as session:
        result = create_write_request(
            session, oauth_client_id=client_id, user_id=owner_id, ontology_id=ontology_id,
            release_id=release_id, descriptor_id="action:x", parameters={},
        )

    other_token = _login(pg_client, other_username)
    detail = pg_client.get(f"/api/v1/mcp/write-requests/{result['request_id']}", headers={"Authorization": f"Bearer {other_token}"})
    assert detail.status_code == 404
    approve = pg_client.post(f"/api/v1/mcp/write-requests/{result['request_id']}/approve", headers={"Authorization": f"Bearer {other_token}"})
    assert approve.status_code == 404


def test_requires_authentication(pg_client):
    resp = pg_client.get("/api/v1/mcp/write-requests")
    assert resp.status_code == 403
