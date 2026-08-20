"""Task 6: full end-to-end path an external MCP client actually takes —
register client, PKCE token mint, tools/list, a read tool call, a write
proposal, human approval via the REST endpoint, and the write tool
observing that approval on its next check_write_status call."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import parse_qs, quote, urlparse

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


@pytest.fixture
def mcp_e2e_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "mcp_e2e_" + uuid.uuid4().hex
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
def pg_client(mcp_e2e_db):
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.main import app

    Session = mcp_e2e_db

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


def test_full_external_mcp_client_journey(pg_client, mcp_e2e_db):
    from app.services import oauth_clients
    from app.services.auth_service import hash_password

    Session = mcp_e2e_db
    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, 'e2eadmin', 'e2eadmin@example.com', :pw, 'admin', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "pw": hash_password("secret123"), "domain": DEFAULT_DOMAIN})
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, 'e2euser', 'e2euser@example.com', :pw, 'editor', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "pw": hash_password("secret123"), "domain": DEFAULT_DOMAIN})
        session.commit()
        admin_id = session.execute(text("SELECT id FROM users WHERE username='e2eadmin'")).scalar_one()
        user_id = session.execute(text("SELECT id FROM users WHERE username='e2euser'")).scalar_one()
        client = oauth_clients.create_client(
            session, client_name="E2E MCP Client", redirect_uris=["https://client.example/cb"],
            allowed_scopes=["ontology:read", "ontology:write"], created_by=admin_id,
        )
        client_id = client.id
        ontology_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO ontology_projects (id, name, domain, version, status, created_by, created_at, updated_at) "
            "VALUES (:id, 'e2e', 'd', 'v0.1', 'draft', :created_by, now(), now())"
        ), {"id": ontology_id, "created_by": admin_id})
        release_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO ontology_releases (id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, created_by, created_at) "
            "VALUES (:id, :oid, 1, 'v1', :mb, '{}'::jsonb, digest(:mb,'sha256'), :uid, now())"
        ), {"id": release_id, "oid": ontology_id, "mb": b"{}", "uid": admin_id})
        session.execute(text(
            "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, status, created_at, revision, created_by) "
            "VALUES (:id, :o, :u, :cap, 'active', now(), 1, :created_by)"
        ), {"id": str(uuid.uuid4()), "o": ontology_id, "u": user_id, "cap": '["execute_instance_action"]', "created_by": admin_id})
        session.commit()

    # 1. PKCE flow
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode("ascii").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    login = pg_client.post("/api/v1/auth/login", json={"username": "e2euser", "password": "secret123"})
    user_token = login.json()["data"]["access_token"]
    consent = pg_client.post(
        "/api/v1/oauth/consent",
        json={"client_id": client_id, "redirect_uri": "https://client.example/cb", "code_challenge": challenge,
              "code_challenge_method": "S256", "scope": "ontology:read ontology:write", "decision": "allow"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    code = parse_qs(urlparse(consent.json()["data"]["redirect_uri"]).query)["code"][0]
    token_resp = pg_client.post(
        "/api/v1/oauth/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": "https://client.example/cb",
              "client_id": client_id, "code_verifier": verifier},
    )
    mcp_token = token_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {mcp_token}"}

    # 2. tools/list
    listed = pg_client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, headers=headers)
    assert {t["name"] for t in listed.json()["result"]["tools"]} >= {"ontology_read_instances", "ontology_propose_write"}

    # 3. a read tool call
    read = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "ontology_read_instances", "arguments": {"ontology_id": ontology_id, "release_id": release_id}}},
        headers=headers,
    )
    assert read.json()["result"]["isError"] is False

    # 4. propose a write
    proposed = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "ontology_propose_write",
                         "arguments": {"ontology_id": ontology_id, "release_id": release_id, "descriptor_id": "action:e2e", "parameters": {"k": "v"}}}},
        headers=headers,
    )
    assert proposed.json()["result"]["isError"] is False
    request_id = json.loads(proposed.json()["result"]["content"][0]["text"])["request_id"]

    # 5. check_write_status before approval -> still pending
    pending_check = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "ontology_check_write_status", "arguments": {"request_id": request_id}}},
        headers=headers,
    )
    assert json.loads(pending_check.json()["result"]["content"][0]["text"])["status"] == "pending"

    # 6. the human approves via the REST endpoint (Task 3), NOT the MCP token
    approve = pg_client.post(f"/api/v1/mcp/write-requests/{request_id}/approve", headers={"Authorization": f"Bearer {user_token}"})
    assert approve.status_code == 200 and approve.json()["data"]["status"] == "approved"

    # 7. the MCP client's own poll now observes the approval
    resolved_check = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": "ontology_check_write_status", "arguments": {"request_id": request_id}}},
        headers=headers,
    )
    assert json.loads(resolved_check.json()["result"]["content"][0]["text"])["status"] == "approved"


def test_oauth_access_token_cannot_approve_via_rest_endpoint(pg_client, mcp_e2e_db):
    from app.services import oauth_clients
    from app.services.auth_service import hash_password

    Session = mcp_e2e_db
    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, 'e2eadmin2', 'e2eadmin2@example.com', :pw, 'admin', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "pw": hash_password("secret123"), "domain": DEFAULT_DOMAIN})
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, 'e2euser2', 'e2euser2@example.com', :pw, 'editor', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "pw": hash_password("secret123"), "domain": DEFAULT_DOMAIN})
        session.commit()
        admin_id = session.execute(text("SELECT id FROM users WHERE username='e2eadmin2'")).scalar_one()
        user_id = session.execute(text("SELECT id FROM users WHERE username='e2euser2'")).scalar_one()
        client = oauth_clients.create_client(
            session, client_name="E2E MCP Client 2", redirect_uris=["https://client.example/cb"],
            allowed_scopes=["ontology:read", "ontology:write"], created_by=admin_id,
        )
        client_id = client.id
        ontology_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO ontology_projects (id, name, domain, version, status, created_by, created_at, updated_at) "
            "VALUES (:id, 'e2e2', 'd', 'v0.1', 'draft', :created_by, now(), now())"
        ), {"id": ontology_id, "created_by": admin_id})
        release_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO ontology_releases (id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, created_by, created_at) "
            "VALUES (:id, :oid, 1, 'v1', :mb, '{}'::jsonb, digest(:mb,'sha256'), :uid, now())"
        ), {"id": release_id, "oid": ontology_id, "mb": b"{}", "uid": admin_id})
        session.execute(text(
            "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, status, created_at, revision, created_by) "
            "VALUES (:id, :o, :u, :cap, 'active', now(), 1, :created_by)"
        ), {"id": str(uuid.uuid4()), "o": ontology_id, "u": user_id, "cap": '["execute_instance_action"]', "created_by": admin_id})
        session.commit()

    # 1. PKCE flow
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode("ascii").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    login = pg_client.post("/api/v1/auth/login", json={"username": "e2euser2", "password": "secret123"})
    user_token = login.json()["data"]["access_token"]
    consent = pg_client.post(
        "/api/v1/oauth/consent",
        json={"client_id": client_id, "redirect_uri": "https://client.example/cb", "code_challenge": challenge,
              "code_challenge_method": "S256", "scope": "ontology:read ontology:write", "decision": "allow"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    code = parse_qs(urlparse(consent.json()["data"]["redirect_uri"]).query)["code"][0]
    token_resp = pg_client.post(
        "/api/v1/oauth/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": "https://client.example/cb",
              "client_id": client_id, "code_verifier": verifier},
    )
    mcp_token = token_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {mcp_token}"}

    # 2. propose a write
    proposed = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "ontology_propose_write",
                         "arguments": {"ontology_id": ontology_id, "release_id": release_id, "descriptor_id": "action:e2e", "parameters": {"k": "v"}}}},
        headers=headers,
    )
    assert proposed.json()["result"]["isError"] is False
    request_id = json.loads(proposed.json()["result"]["content"][0]["text"])["request_id"]

    # 3. the MCP client's own OAuth access token is rejected by the
    # human-facing REST approve endpoint — only an interactive JWT works there
    approve = pg_client.post(f"/api/v1/mcp/write-requests/{request_id}/approve", headers={"Authorization": f"Bearer {mcp_token}"})
    assert approve.status_code == 401
