"""Task 4: HTTP-level contract for the OAuth authorize/consent/token/revoke
endpoints, plus admin client CRUD."""
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import parse_qs, urlparse
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
def oauth_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "oauth_router_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    result = _alembic(schema, "upgrade", "0016_oauth_pkce")
    assert result.returncode == 0, result.stderr
    session_engine = create_engine(_scoped_url(schema))
    Session = sessionmaker(bind=session_engine)
    yield Session
    session_engine.dispose()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


@pytest.fixture
def pg_client(oauth_db):
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.main import app

    Session = oauth_db

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


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode("ascii").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


def test_admin_can_register_client_editor_cannot(pg_client, oauth_db):
    admin_username = "admin-" + uuid.uuid4().hex[:8]
    _add_user(oauth_db, admin_username, role="admin")
    admin_token = _login(pg_client, admin_username)
    resp = pg_client.post(
        "/api/v1/oauth/clients",
        json={"client_name": "Test Client", "redirect_uris": ["https://client.example/cb"], "allowed_scopes": ["a"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201, resp.text
    client_id = resp.json()["data"]["id"]
    assert client_id

    viewer_username = "viewer-" + uuid.uuid4().hex[:8]
    _add_user(oauth_db, viewer_username, role="viewer")
    viewer_token = _login(pg_client, viewer_username)
    denied = pg_client.post(
        "/api/v1/oauth/clients",
        json={"client_name": "X", "redirect_uris": [], "allowed_scopes": []},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert denied.status_code == 403


def test_authorize_rejects_unregistered_redirect_uri_without_redirecting(pg_client, oauth_db):
    admin_username = "admin-" + uuid.uuid4().hex[:8]
    _add_user(oauth_db, admin_username, role="admin")
    admin_token = _login(pg_client, admin_username)
    client_resp = pg_client.post(
        "/api/v1/oauth/clients",
        json={"client_name": "X", "redirect_uris": ["https://client.example/cb"], "allowed_scopes": ["a"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    client_id = client_resp.json()["data"]["id"]
    _, challenge = _pkce_pair()
    resp = pg_client.get(
        "/api/v1/oauth/authorize",
        params={
            "response_type": "code", "client_id": client_id, "redirect_uri": "https://evil.example/cb",
            "code_challenge": challenge, "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400  # never redirected to an unregistered URI


def test_authorize_redirects_to_consent_page_for_valid_request(pg_client, oauth_db):
    admin_username = "admin-" + uuid.uuid4().hex[:8]
    _add_user(oauth_db, admin_username, role="admin")
    admin_token = _login(pg_client, admin_username)
    client_resp = pg_client.post(
        "/api/v1/oauth/clients",
        json={"client_name": "X", "redirect_uris": ["https://client.example/cb"], "allowed_scopes": ["a"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    client_id = client_resp.json()["data"]["id"]
    _, challenge = _pkce_pair()
    resp = pg_client.get(
        "/api/v1/oauth/authorize",
        params={
            "response_type": "code", "client_id": client_id, "redirect_uri": "https://client.example/cb",
            "code_challenge": challenge, "code_challenge_method": "S256", "scope": "a", "state": "xyz",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("http://localhost:5173/oauth/consent?")
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == [client_id]
    assert query["state"] == ["xyz"]


def test_full_authorize_consent_token_refresh_revoke_flow(pg_client, oauth_db):
    admin_username = "admin-" + uuid.uuid4().hex[:8]
    _add_user(oauth_db, admin_username, role="admin")
    admin_token = _login(pg_client, admin_username)
    client_resp = pg_client.post(
        "/api/v1/oauth/clients",
        json={"client_name": "X", "redirect_uris": ["https://client.example/cb"], "allowed_scopes": ["a", "b"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    client_id = client_resp.json()["data"]["id"]

    end_user_username = "enduser-" + uuid.uuid4().hex[:8]
    _add_user(oauth_db, end_user_username, role="viewer")
    end_user_token = _login(pg_client, end_user_username)

    verifier, challenge = _pkce_pair()
    consent_resp = pg_client.post(
        "/api/v1/oauth/consent",
        json={
            "client_id": client_id, "redirect_uri": "https://client.example/cb",
            "code_challenge": challenge, "code_challenge_method": "S256",
            "scope": "a", "state": "xyz", "decision": "allow",
        },
        headers={"Authorization": f"Bearer {end_user_token}"},
    )
    assert consent_resp.status_code == 200, consent_resp.text
    redirect_uri = consent_resp.json()["data"]["redirect_uri"]
    query = parse_qs(urlparse(redirect_uri).query)
    assert query["state"] == ["xyz"]
    code = query["code"][0]

    token_resp = pg_client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": "https://client.example/cb",
            "client_id": client_id, "code_verifier": verifier,
        },
    )
    assert token_resp.status_code == 200, token_resp.text
    body = token_resp.json()
    assert body["token_type"] == "Bearer" and body["scope"] == "a" and body["expires_in"] > 0
    refresh_token = body["refresh_token"]

    refresh_resp = pg_client.post(
        "/api/v1/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
    )
    assert refresh_resp.status_code == 200, refresh_resp.text
    new_refresh = refresh_resp.json()["refresh_token"]
    assert new_refresh != refresh_token

    # the old refresh token is now stale (reuse) -> invalid_grant
    reuse_resp = pg_client.post(
        "/api/v1/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
    )
    assert reuse_resp.status_code == 400
    assert reuse_resp.json()["error"] == "invalid_grant"

    revoke_resp = pg_client.post("/api/v1/oauth/revoke", data={"token": new_refresh, "client_id": client_id})
    assert revoke_resp.status_code == 200

    post_revoke = pg_client.post(
        "/api/v1/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": new_refresh, "client_id": client_id},
    )
    assert post_revoke.status_code == 400


def test_consent_deny_redirects_with_access_denied(pg_client, oauth_db):
    admin_username = "admin-" + uuid.uuid4().hex[:8]
    _add_user(oauth_db, admin_username, role="admin")
    admin_token = _login(pg_client, admin_username)
    client_resp = pg_client.post(
        "/api/v1/oauth/clients",
        json={"client_name": "X", "redirect_uris": ["https://client.example/cb"], "allowed_scopes": ["a"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    client_id = client_resp.json()["data"]["id"]
    end_user_username = "denyuser-" + uuid.uuid4().hex[:8]
    _add_user(oauth_db, end_user_username, role="viewer")
    end_user_token = _login(pg_client, end_user_username)
    _, challenge = _pkce_pair()
    resp = pg_client.post(
        "/api/v1/oauth/consent",
        json={
            "client_id": client_id, "redirect_uri": "https://client.example/cb",
            "code_challenge": challenge, "code_challenge_method": "S256", "decision": "deny",
        },
        headers={"Authorization": f"Bearer {end_user_token}"},
    )
    assert resp.status_code == 200
    query = parse_qs(urlparse(resp.json()["data"]["redirect_uri"]).query)
    assert query["error"] == ["access_denied"]


def test_token_endpoint_uses_form_encoding_not_json(pg_client, oauth_db):
    resp = pg_client.post("/api/v1/oauth/token", json={"grant_type": "authorization_code"})
    assert resp.status_code == 422  # FastAPI rejects a JSON body against Form(...) params


def test_public_client_lookup_hides_inactive_clients(pg_client, oauth_db):
    admin_username = "admin-" + uuid.uuid4().hex[:8]
    _add_user(oauth_db, admin_username, role="admin")
    admin_token = _login(pg_client, admin_username)
    client_resp = pg_client.post(
        "/api/v1/oauth/clients",
        json={"client_name": "Visible Client", "redirect_uris": [], "allowed_scopes": []},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    client_id = client_resp.json()["data"]["id"]
    ok = pg_client.get(f"/api/v1/oauth/clients/{client_id}")
    assert ok.status_code == 200
    assert ok.json()["data"]["client_name"] == "Visible Client"
    assert ok.json()["data"] == {"client_id": client_id, "client_name": "Visible Client"}
    missing = pg_client.get("/api/v1/oauth/clients/nonexistent")
    assert missing.status_code == 404
