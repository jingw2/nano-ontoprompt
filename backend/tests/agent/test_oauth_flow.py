"""Task 3: PKCE verification, authorization-code exchange, refresh rotation
with reuse detection, and cross-token-type (interactive vs. OAuth) guards."""
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from urllib.parse import quote

import pytest
from fastapi import HTTPException
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
    schema = "oauth_flow_" + uuid.uuid4().hex
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


def _add_user(Session, username, active=True):
    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :username, :email, 'x', 'viewer', :active, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "username": username, "email": f"{username}@example.com",
            "active": active, "domain": DEFAULT_DOMAIN})
        session.commit()
        return session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).scalar_one()


def _add_client(Session, admin_id, redirect_uri="https://client.example/cb"):
    from app.services import oauth_clients
    with Session() as session:
        client = oauth_clients.create_client(
            session, client_name="X", redirect_uris=[redirect_uri],
            allowed_scopes=["a", "b"], created_by=admin_id,
        )
        return client.id


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode("ascii").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


# ── PKCE verification (DB-free) ──────────────────────────────────────────────

def test_validate_code_challenge_rejects_plain_and_malformed():
    from app.services.oauth_flow import InvalidRequestError, validate_code_challenge

    _, challenge = _pkce_pair()
    validate_code_challenge(challenge, "S256")  # does not raise
    with pytest.raises(InvalidRequestError):
        validate_code_challenge(challenge, "plain")
    with pytest.raises(InvalidRequestError):
        validate_code_challenge("too-short", "S256")


# ── Authorization code issuance and exchange ─────────────────────────────────

def test_issue_and_exchange_authorization_code(oauth_db):
    from app.services.oauth_flow import exchange_authorization_code, issue_authorization_code

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    verifier, challenge = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a b",
        )
        access_token, refresh_token, scope, expires_in = exchange_authorization_code(
            session, code=code, client_id=client_id, redirect_uri="https://client.example/cb", code_verifier=verifier,
        )
        assert access_token and refresh_token and scope == "a b" and expires_in > 0


def test_exchange_rejects_wrong_verifier(oauth_db):
    from app.services.oauth_flow import InvalidGrantError, exchange_authorization_code, issue_authorization_code

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    verifier, challenge = _pkce_pair()
    wrong_verifier, _ = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a",
        )
        with pytest.raises(InvalidGrantError):
            exchange_authorization_code(
                session, code=code, client_id=client_id, redirect_uri="https://client.example/cb",
                code_verifier=wrong_verifier,
            )


def test_exchange_is_single_use(oauth_db):
    from app.services.oauth_flow import InvalidGrantError, exchange_authorization_code, issue_authorization_code

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    verifier, challenge = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a",
        )
        exchange_authorization_code(
            session, code=code, client_id=client_id, redirect_uri="https://client.example/cb", code_verifier=verifier,
        )
        with pytest.raises(InvalidGrantError):
            exchange_authorization_code(
                session, code=code, client_id=client_id, redirect_uri="https://client.example/cb", code_verifier=verifier,
            )


def test_exchange_rejects_redirect_uri_mismatch(oauth_db):
    from app.services.oauth_flow import InvalidGrantError, exchange_authorization_code, issue_authorization_code

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    verifier, challenge = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a",
        )
        with pytest.raises(InvalidGrantError):
            exchange_authorization_code(
                session, code=code, client_id=client_id, redirect_uri="https://client.example/DIFFERENT",
                code_verifier=verifier,
            )


# ── Refresh rotation, reuse detection, revocation ────────────────────────────

def test_rotate_refresh_yields_new_pair_and_reuse_revokes_family(oauth_db):
    from app.services.oauth_flow import (
        InvalidGrantError, exchange_authorization_code, issue_authorization_code, rotate_oauth_refresh,
    )

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    verifier, challenge = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a",
        )
        _, refresh_token, _, _ = exchange_authorization_code(
            session, code=code, client_id=client_id, redirect_uri="https://client.example/cb", code_verifier=verifier,
        )
        access2, refresh2, _, _ = rotate_oauth_refresh(session, refresh_token=refresh_token, client_id=client_id)
        assert access2 and refresh2 and refresh2 != refresh_token
        with pytest.raises(InvalidGrantError):
            rotate_oauth_refresh(session, refresh_token=refresh_token, client_id=client_id)  # stale generation
        status = session.execute(text(
            "SELECT status FROM oauth_refresh_families WHERE user_id=:id"
        ), {"id": user_id}).scalar_one()
        assert status == "revoked"


def test_rotate_rejects_wrong_client_id(oauth_db):
    from app.services.oauth_flow import (
        InvalidGrantError, exchange_authorization_code, issue_authorization_code, rotate_oauth_refresh,
    )

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    other_client_id = _add_client(Session, admin_id, redirect_uri="https://other.example/cb")
    verifier, challenge = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a",
        )
        _, refresh_token, _, _ = exchange_authorization_code(
            session, code=code, client_id=client_id, redirect_uri="https://client.example/cb", code_verifier=verifier,
        )
        with pytest.raises(InvalidGrantError):
            rotate_oauth_refresh(session, refresh_token=refresh_token, client_id=other_client_id)


def test_rotate_rejects_inactive_user(oauth_db):
    from app.services.oauth_flow import (
        InvalidGrantError, exchange_authorization_code, issue_authorization_code, rotate_oauth_refresh,
    )

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    verifier, challenge = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a",
        )
        _, refresh_token, _, _ = exchange_authorization_code(
            session, code=code, client_id=client_id, redirect_uri="https://client.example/cb", code_verifier=verifier,
        )
        session.execute(text("UPDATE users SET is_active=false WHERE id=:id"), {"id": user_id})
        session.commit()
        with pytest.raises(InvalidGrantError):
            rotate_oauth_refresh(session, refresh_token=refresh_token, client_id=client_id)


def test_revoke_disables_further_rotation(oauth_db):
    from app.services.oauth_flow import (
        InvalidGrantError, exchange_authorization_code, issue_authorization_code,
        revoke_oauth_refresh, rotate_oauth_refresh,
    )

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    verifier, challenge = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a",
        )
        _, refresh_token, _, _ = exchange_authorization_code(
            session, code=code, client_id=client_id, redirect_uri="https://client.example/cb", code_verifier=verifier,
        )
        revoke_oauth_refresh(session, refresh_token=refresh_token, client_id=client_id)
        with pytest.raises(InvalidGrantError):
            rotate_oauth_refresh(session, refresh_token=refresh_token, client_id=client_id)
        # revoking an unknown token is a silent no-op, not an error
        revoke_oauth_refresh(session, refresh_token="unknown-token", client_id=client_id)


def test_deactivated_client_cannot_exchange_outstanding_code(oauth_db):
    from app.services import oauth_clients
    from app.services.oauth_flow import InvalidGrantError, exchange_authorization_code, issue_authorization_code

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    verifier, challenge = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a",
        )
    with Session() as session:
        oauth_clients.deactivate_client(session, client_id)
    with Session() as session:
        with pytest.raises(InvalidGrantError):
            exchange_authorization_code(
                session, code=code, client_id=client_id, redirect_uri="https://client.example/cb",
                code_verifier=verifier,
            )


def test_deactivated_client_cannot_rotate_refresh_token(oauth_db):
    from app.services import oauth_clients
    from app.services.oauth_flow import (
        InvalidGrantError, exchange_authorization_code, issue_authorization_code, rotate_oauth_refresh,
    )

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    verifier, challenge = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a",
        )
        _, refresh_token, _, _ = exchange_authorization_code(
            session, code=code, client_id=client_id, redirect_uri="https://client.example/cb", code_verifier=verifier,
        )
    with Session() as session:
        oauth_clients.deactivate_client(session, client_id)
    with Session() as session:
        with pytest.raises(InvalidGrantError):
            rotate_oauth_refresh(session, refresh_token=refresh_token, client_id=client_id)


# ── Cross-token-type guards ───────────────────────────────────────────────────

def test_oauth_access_token_carries_token_use_claim():
    from app.services.auth_service import create_oauth_access_token, decode_token

    token = create_oauth_access_token("user-1", "client-1", "a b")
    payload = decode_token(token)
    assert payload["token_use"] == "oauth_access"
    assert payload["sub"] == "user-1"
    assert payload["client_id"] == "client-1"
    assert payload["scope"] == "a b"


def test_get_current_user_rejects_oauth_access_token(oauth_db):
    from app.deps import get_current_user
    from app.services.auth_service import create_oauth_access_token
    from fastapi.security import HTTPAuthorizationCredentials

    Session = oauth_db
    user_id = _add_user(Session, "guarded-" + uuid.uuid4().hex[:8])
    token = create_oauth_access_token(user_id, "client-1", "a")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with Session() as session:
        with pytest.raises(HTTPException) as excinfo:
            get_current_user(credentials=creds, db=session)
        assert excinfo.value.status_code == 401


def test_get_oauth_context_rejects_interactive_token(oauth_db):
    from app.deps.oauth import get_oauth_context
    from app.services.auth_service import create_access_token
    from fastapi.security import HTTPAuthorizationCredentials

    Session = oauth_db
    user_id = _add_user(Session, "interactive-" + uuid.uuid4().hex[:8])
    token = create_access_token({"sub": user_id, "role": "viewer"})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with Session() as session:
        with pytest.raises(HTTPException) as excinfo:
            get_oauth_context(credentials=creds, db=session)
        assert excinfo.value.status_code == 401


def test_get_oauth_context_accepts_valid_oauth_token_and_rejects_inactive_client(oauth_db):
    from app.deps.oauth import get_oauth_context
    from app.services import oauth_clients
    from app.services.auth_service import create_oauth_access_token
    from fastapi.security import HTTPAuthorizationCredentials

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "oauthuser-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    token = create_oauth_access_token(user_id, client_id, "a b")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with Session() as session:
        ctx = get_oauth_context(credentials=creds, db=session)
        assert ctx.user_id == user_id and ctx.client_id == client_id and ctx.scope == {"a", "b"}

    with Session() as session:
        oauth_clients.deactivate_client(session, client_id)
    with Session() as session:
        with pytest.raises(HTTPException) as excinfo:
            get_oauth_context(credentials=creds, db=session)
        assert excinfo.value.status_code == 401


# ── Concurrency (row-locking) ─────────────────────────────────────────────────

def test_concurrent_code_exchange_yields_exactly_one_success(oauth_db):
    from app.services.oauth_flow import InvalidGrantError, exchange_authorization_code, issue_authorization_code

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    verifier, challenge = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a",
        )

    outcomes = []
    barrier = threading.Barrier(2)

    def attempt():
        local_session = Session()
        try:
            barrier.wait(timeout=10)
            exchange_authorization_code(
                local_session, code=code, client_id=client_id,
                redirect_uri="https://client.example/cb", code_verifier=verifier,
            )
            outcomes.append("success")
        except InvalidGrantError:
            outcomes.append("rejected")
        finally:
            local_session.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(outcomes) == ["rejected", "success"]


def test_concurrent_refresh_rotation_yields_exactly_one_success(oauth_db):
    from app.services.oauth_flow import (
        InvalidGrantError, exchange_authorization_code, issue_authorization_code, rotate_oauth_refresh,
    )

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    verifier, challenge = _pkce_pair()
    with Session() as session:
        code = issue_authorization_code(
            session, client_id=client_id, user_id=user_id, redirect_uri="https://client.example/cb",
            code_challenge=challenge, code_challenge_method="S256", scope="a",
        )
        _, refresh_token, _, _ = exchange_authorization_code(
            session, code=code, client_id=client_id, redirect_uri="https://client.example/cb", code_verifier=verifier,
        )

    outcomes = []
    barrier = threading.Barrier(2)

    def attempt():
        local_session = Session()
        try:
            barrier.wait(timeout=10)
            rotate_oauth_refresh(local_session, refresh_token=refresh_token, client_id=client_id)
            outcomes.append("success")
        except InvalidGrantError:
            outcomes.append("rejected")
        finally:
            local_session.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(outcomes) == ["rejected", "success"]
