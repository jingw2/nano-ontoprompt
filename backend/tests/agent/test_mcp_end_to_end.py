"""Task 6: full end-to-end path an external MCP client actually takes —
register client, PKCE token mint, tools/list, a read tool call, a write
proposal, human approval via the REST endpoint, and the write tool
observing that approval on its next check_write_status call."""
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
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
    # "head", not the 0017 revision that introduced mcp_write_requests: this
    # test creates OAuthClient rows via app.services.oauth_clients, which
    # Task 13 (0029_runtime_identity) extended with new non-nullable columns.
    result = _alembic(schema, "upgrade", "head")
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


def test_runtime_tools_route_through_runtime_service_and_ignore_identity_arguments(pg_client, mcp_e2e_db):
    """Task 18: the runtime_* MCP tools resolve through the exact same
    RuntimeService (Task 15) REST and the SDK use — same governed snapshot
    pin, same capability/entitlement intersection, same denial vocabulary —
    over the real OAuth-authenticated JSON-RPC transport, and a caller-
    supplied agent_id/user_id argument never overrides the verified OAuth
    identity."""
    from app.services import oauth_clients
    from app.services.auth_service import hash_password

    Session = mcp_e2e_db
    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, 'e2eruntimeadmin', 'e2eruntimeadmin@example.com', :pw, 'admin', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "pw": hash_password("secret123"), "domain": DEFAULT_DOMAIN})
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, 'e2eruntimeuser', 'e2eruntimeuser@example.com', :pw, 'editor', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "pw": hash_password("secret123"), "domain": DEFAULT_DOMAIN})
        session.commit()
        admin_id = session.execute(text("SELECT id FROM users WHERE username='e2eruntimeadmin'")).scalar_one()
        user_id = session.execute(text("SELECT id FROM users WHERE username='e2eruntimeuser'")).scalar_one()
        client = oauth_clients.create_client(
            session, client_name="E2E Runtime MCP Client", redirect_uris=["https://client.example/cb"],
            allowed_scopes=["ontology:read", "ontology:write"], created_by=admin_id,
        )
        client_id = client.id
        # `create_client` only sets the OAuth-era columns; capability_names
        # is Task 13's Runtime-identity registration column, defaulting to
        # `[]` — grant this Agent identity both Runtime capabilities.
        session.execute(text(
            "UPDATE oauth_clients SET capability_names = :caps WHERE id = :id"
        ), {"caps": '["investigate", "propose_action"]', "id": client_id})

        ontology_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO ontology_projects (id, name, domain, version, status, created_by, created_at, updated_at) "
            "VALUES (:id, 'e2e-runtime', 'd', 'v0.1', 'draft', :created_by, now(), now())"
        ), {"id": ontology_id, "created_by": admin_id})
        release_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO ontology_releases (id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, created_by, created_at) "
            "VALUES (:id, :oid, 1, 'v1', :mb, '{}'::jsonb, digest(:mb,'sha256'), :uid, now())"
        ), {"id": release_id, "oid": ontology_id, "mb": b"{}", "uid": admin_id})
        # `evaluate_access` requires the pinned release to be the ontology's
        # CURRENT published release, not merely `status = 'published'`.
        session.execute(text(
            "UPDATE ontology_projects SET latest_published_release_id = :rid WHERE id = :oid"
        ), {"rid": release_id, "oid": ontology_id})
        snapshot_id = str(uuid.uuid4())
        # Task 20 freshness gate: a snapshot with no governed refresh
        # context defaults to freshness_state='unknown' and is denied
        # outright by the Runtime freshness gate (see tests/runtime/
        # test_runtime_api.py) — this e2e test exercises the ALLOW path,
        # so the snapshot must carry real (fresh) freshness pins.
        source_cursor = json.dumps({
            "source_id": "e2e-runtime-source", "resource": "default",
            "contract": "watermark_primary_key", "watermark": None, "primary_key": "1",
            "opaque_value": None, "observed_at": datetime.now(timezone.utc).isoformat(),
        })
        session.execute(text(
            "INSERT INTO semantic_snapshots (id, ontology_release_id, quality_summary, evidence_summary, "
            "materialization_hash, status, freshness_state, freshness_lag_seconds, source_cursor, "
            "created_by, created_at) "
            "VALUES (:id, :rid, :quality, :evidence, :mhash, 'materialized', 'fresh', 60, "
            "CAST(:cursor AS json), :uid, now())"
        ), {"id": snapshot_id, "rid": release_id, "quality": '{"row_count": 0}', "evidence": '{"citations": []}',
            "mhash": "a" * 64, "cursor": source_cursor, "uid": admin_id})
        session.execute(text(
            "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, status, created_at, revision, created_by) "
            "VALUES (:id, :o, :u, :cap, 'active', now(), 1, :created_by)"
        ), {"id": str(uuid.uuid4()), "o": ontology_id, "u": user_id, "cap": '["investigate", "propose_action"]', "created_by": admin_id})
        action_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO actions (id, ontology_id, name_cn, linked_entities, linked_logic_ids, confidence, version, "
            "enabled, status, created_at, updated_at) "
            "VALUES (:id, :oid, 'e2e action', '[]', '[]', 1.0, 'v0.1', true, 'draft', now(), now())"
        ), {"id": action_id, "oid": ontology_id})
        session.commit()

    # 1. PKCE flow
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode("ascii").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    login = pg_client.post("/api/v1/auth/login", json={"username": "e2eruntimeuser", "password": "secret123"})
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

    # 2. runtime_investigate: a caller-supplied agent_id/user_id in
    # arguments must never override the verified OAuth identity
    investigate = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "runtime_investigate",
                         "arguments": {"semantic_snapshot_id": snapshot_id, "ontology_id": ontology_id,
                                       "agent_id": "agent-other", "user_id": "user-other"}}},
        headers=headers,
    )
    assert investigate.json()["result"]["isError"] is False
    inv_result = json.loads(investigate.json()["result"]["content"][0]["text"])
    assert inv_result["decision"] == "ALLOW"
    assert inv_result["semantic_snapshot_id"] == snapshot_id
    assert inv_result["agent_id"] == client_id
    assert inv_result["user_id"] == user_id

    # 3. runtime_create_action_plan: same identity-authority guarantee
    plan_resp = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "runtime_create_action_plan",
                         "arguments": {"semantic_snapshot_id": snapshot_id, "action_id": action_id,
                                       "parameters": {"k": "v"}, "agent_id": "agent-other", "user_id": "user-other"}}},
        headers=headers,
    )
    assert plan_resp.json()["result"]["isError"] is False
    plan = json.loads(plan_resp.json()["result"]["content"][0]["text"])
    assert plan["agent_id"] == client_id
    assert plan["user_id"] == user_id

    # 4. runtime_get_action_plan / runtime_get_execution_status
    fetched = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "runtime_get_action_plan", "arguments": {"plan_id": plan["id"]}}},
        headers=headers,
    )
    assert json.loads(fetched.json()["result"]["content"][0]["text"])["plan_hash"] == plan["plan_hash"]

    status_resp = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "runtime_get_execution_status", "arguments": {"plan_id": plan["id"]}}},
        headers=headers,
    )
    assert json.loads(status_resp.json()["result"]["content"][0]["text"])["status"] == "not_started"

    # 5. a legacy release-only investigate request (no materialized
    # snapshot) is still denied the same way RuntimeService denies it
    # directly — never answered with a data result.
    denied = pg_client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": "runtime_investigate", "arguments": {"release_id": release_id}}},
        headers=headers,
    )
    assert denied.json()["result"]["isError"] is True
    assert "SNAPSHOT_NOT_GOVERNED" in denied.json()["result"]["content"][0]["text"]
