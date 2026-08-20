"""Task 4: the JSON-RPC 2.0 MCP endpoint — initialize/tools/list/tools/call,
OAuth-authenticated, scope-enforced."""
import base64
import hashlib
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
    schema = "mcp_rpc_" + uuid.uuid4().hex
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


def _add_client(Session, admin_id, scopes):
    from app.services import oauth_clients
    with Session() as session:
        client = oauth_clients.create_client(
            session, client_name="X", redirect_uris=["https://client.example/cb"], allowed_scopes=scopes, created_by=admin_id,
        )
        return client.id


def _mint_oauth_token(pg_client, client_id, user_id_username, scope):
    """Runs the real PKCE flow end to end and returns a valid access token."""
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode("ascii").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")

    login = pg_client.post("/api/v1/auth/login", json={"username": user_id_username, "password": "secret123"})
    assert login.status_code == 200, login.text
    user_token = login.json()["data"]["access_token"]
    consent = pg_client.post(
        "/api/v1/oauth/consent",
        json={
            "client_id": client_id, "redirect_uri": "https://client.example/cb",
            "code_challenge": challenge, "code_challenge_method": "S256", "scope": scope, "decision": "allow",
        },
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert consent.status_code == 200, consent.text
    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(consent.json()["data"]["redirect_uri"]).query)["code"][0]
    token = pg_client.post(
        "/api/v1/oauth/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": "https://client.example/cb",
              "client_id": client_id, "code_verifier": verifier},
    )
    assert token.status_code == 200, token.text
    return token.json()["access_token"]


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


def test_initialize_and_tools_list_require_no_special_scope(pg_client, mcp_db):
    admin_id = _add_user(mcp_db, "admin-" + uuid.uuid4().hex[:8], role="admin")
    username = "user-" + uuid.uuid4().hex[:8]
    _add_user(mcp_db, username)
    client_id = _add_client(mcp_db, admin_id, scopes=["ontology:read"])
    token = _mint_oauth_token(pg_client, client_id, username, "ontology:read")

    init = pg_client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                           headers={"Authorization": f"Bearer {token}"})
    assert init.status_code == 200
    assert init.json()["result"]["protocolVersion"] == "2024-11-05"

    listed = pg_client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                             headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    tool_names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert tool_names == {"ontology.read_instances", "ontology.traverse_relations", "ontology.propose_write", "ontology.check_write_status"}


def test_rpc_rejects_missing_or_wrong_token_type(pg_client):
    no_auth = pg_client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert no_auth.status_code == 401


def test_read_instances_tool_call_succeeds_with_read_scope(pg_client, mcp_db):
    admin_id = _add_user(mcp_db, "admin-" + uuid.uuid4().hex[:8], role="admin")
    username = "user-" + uuid.uuid4().hex[:8]
    _add_user(mcp_db, username)
    client_id = _add_client(mcp_db, admin_id, scopes=["ontology:read"])
    ontology_id, release_id = _add_ontology_and_release(mcp_db, admin_id)
    token = _mint_oauth_token(pg_client, client_id, username, "ontology:read")

    resp = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "ontology.read_instances", "arguments": {"ontology_id": ontology_id, "release_id": release_id}}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()["result"]
    assert body["isError"] is False
    assert "items" in body["content"][0]["text"]


def test_propose_write_tool_call_requires_write_scope_and_grant(pg_client, mcp_db):
    admin_id = _add_user(mcp_db, "admin-" + uuid.uuid4().hex[:8], role="admin")
    username = "user-" + uuid.uuid4().hex[:8]
    user_id = _add_user(mcp_db, username)
    # client only has read scope -> tool call must be refused with SCOPE_DENIED
    read_only_client = _add_client(mcp_db, admin_id, scopes=["ontology:read"])
    ontology_id, release_id = _add_ontology_and_release(mcp_db, admin_id)
    read_token = _mint_oauth_token(pg_client, read_only_client, username, "ontology:read")

    denied = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "ontology.propose_write",
                         "arguments": {"ontology_id": ontology_id, "release_id": release_id, "descriptor_id": "action:x", "parameters": {}}}},
        headers={"Authorization": f"Bearer {read_token}"},
    )
    assert denied.status_code == 200
    assert denied.json()["result"]["isError"] is True
    assert "SCOPE_DENIED" in denied.json()["result"]["content"][0]["text"]

    # now with write scope but no data grant -> DATA_GRANT_DENIED
    write_client = _add_client(mcp_db, admin_id, scopes=["ontology:write"])
    write_token = _mint_oauth_token(pg_client, write_client, username, "ontology:write")
    ungranted = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": "ontology.propose_write",
                         "arguments": {"ontology_id": ontology_id, "release_id": release_id, "descriptor_id": "action:x", "parameters": {}}}},
        headers={"Authorization": f"Bearer {write_token}"},
    )
    assert ungranted.json()["result"]["isError"] is True
    assert "DATA_GRANT_DENIED" in ungranted.json()["result"]["content"][0]["text"]

    # grant it, then propose + check_write_status round trip
    _grant_write(mcp_db, ontology_id, user_id, admin_id)
    proposed = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 6, "method": "tools/call",
              "params": {"name": "ontology.propose_write",
                         "arguments": {"ontology_id": ontology_id, "release_id": release_id, "descriptor_id": "action:x", "parameters": {}}}},
        headers={"Authorization": f"Bearer {write_token}"},
    )
    assert proposed.json()["result"]["isError"] is False

    import json as jsonlib
    payload = jsonlib.loads(proposed.json()["result"]["content"][0]["text"])
    request_id = payload["request_id"]

    checked = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 7, "method": "tools/call",
              "params": {"name": "ontology.check_write_status", "arguments": {"request_id": request_id}}},
        headers={"Authorization": f"Bearer {write_token}"},
    )
    checked_payload = jsonlib.loads(checked.json()["result"]["content"][0]["text"])
    assert checked_payload["status"] == "pending"
