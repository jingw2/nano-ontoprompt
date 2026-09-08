"""F0-AUTH: rotating browser refresh families.

Login/refresh uses an HttpOnly, Secure, SameSite=Strict cookie scoped to
Path=/api/v1/auth; the family rotates one generation per refresh, reuse of a
stale generation revokes the whole family, and password mutation revokes every
family atomically.  Refresh/logout are cookie-authenticated and require an
origin check plus a double-submit CSRF token; ordinary bearer endpoints reject
cookie-only authorization.

PostgreSQL-marked tests use TEST_DATABASE_URL; SQLite never substitutes.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[2]
SERVICE = BACKEND_DIR / "app" / "services" / "auth_refresh.py"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"
REFRESH_COOKIE = "ontexus_refresh"
CSRF_COOKIE = "csrf_token"


def test_f0_auth_red_contract():
    failures = []
    if not SERVICE.exists():
        failures.append("auth_refresh service missing")
    else:
        for name in ("rotate_refresh_session", "revoke_refresh_families", "hash_refresh_token"):
            if name not in SERVICE.read_text():
                failures.append(f"auth_refresh missing {name}")
    router_source = (BACKEND_DIR / "app" / "routers" / "auth.py").read_text()
    for marker in ('"/refresh"', '"/logout"', "X-CSRF-Token", "httponly=True"):
        if marker not in router_source:
            failures.append(f"auth router missing {marker}")
    if failures:
        pytest.fail("RED_F0_AUTH: " + "; ".join(failures))


# ── DB-free token contract ───────────────────────────────────────────────────

def test_refresh_token_hashing_is_deterministic_and_not_reversible():
    from app.services.auth_refresh import hash_refresh_token, issue_refresh_token

    token = issue_refresh_token()
    assert isinstance(token, str) and len(token) >= 40
    assert hash_refresh_token(token) == hashlib.sha256(token.encode()).hexdigest()
    assert hash_refresh_token(token) != token
    assert hash_refresh_token(token) != hash_refresh_token(token + "x")
    assert issue_refresh_token() != issue_refresh_token()


def _scoped_url(schema):
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture(scope="module")
def refresh_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "f0_auth_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    result = _alembic(schema, "upgrade", "0003_publication_governance")
    assert result.returncode == 0, result.stderr
    session_engine = create_engine(_scoped_url(schema))
    Session = sessionmaker(bind=session_engine)
    yield Session, session_engine
    session_engine.dispose()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _add_user(Session, username, password="secret123"):
    from app.services.auth_service import hash_password

    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :username, :email, :password_hash, 'viewer', true, now(), now(), :domain)"
        ), {
            "id": str(uuid.uuid4()),
            "username": username,
            "email": f"{username}@example.com",
            "password_hash": hash_password(password),
            "domain": DEFAULT_DOMAIN,
        })
        user_id = session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).scalar_one()
        session.commit()
        return user_id


# ── PostgreSQL rotation, reuse, revocation, and concurrency ──────────────────

def test_zz_rotation_one_successor_reuse_revokes_family(refresh_db):
    from app.services.auth_refresh import (
        RefreshReuseError,
        create_refresh_session,
        rotate_refresh_session,
    )

    Session, _ = refresh_db
    user_id = _add_user(Session, "rotate-" + uuid.uuid4().hex[:8])
    with Session() as session:
        token = create_refresh_session(session, user_id)
        assert token
        access, successor = rotate_refresh_session(session, token)
        assert access and successor and successor != token
        with pytest.raises(RefreshReuseError):
            rotate_refresh_session(session, token)  # stale generation -> reuse
        family_status = session.execute(text(
            "SELECT status FROM auth_refresh_families WHERE user_id=:id"
        ), {"id": user_id}).scalar_one()
        assert family_status == "revoked"
        # the revoked family cannot rotate the successor either
        with pytest.raises(Exception):
            rotate_refresh_session(session, successor)


def test_zz_rotation_reloads_current_role_and_rejects_inactive_user(refresh_db):
    from app.services.auth_refresh import (
        RefreshRevokedError,
        create_refresh_session,
        rotate_refresh_session,
    )

    Session, _ = refresh_db
    user_id = _add_user(Session, "inactive-" + uuid.uuid4().hex[:8])
    with Session() as session:
        token = create_refresh_session(session, user_id)
    with Session() as session:
        session.execute(text("UPDATE users SET is_active=false WHERE id=:id"), {"id": user_id})
        session.commit()
    with Session() as session:
        with pytest.raises(RefreshRevokedError):
            rotate_refresh_session(session, token)
        assert session.execute(text(
            "SELECT status FROM auth_refresh_families WHERE user_id=:id"
        ), {"id": user_id}).scalar_one() == "revoked"


def test_zz_concurrent_rotation_yields_exactly_one_success_and_one_reuse(refresh_db):
    from app.services.auth_refresh import RefreshReuseError, create_refresh_session, rotate_refresh_session

    Session, engine = refresh_db
    user_id = _add_user(Session, "concurrent-" + uuid.uuid4().hex[:8])
    with Session() as session:
        token = create_refresh_session(session, user_id)

    outcomes = []
    barrier = threading.Barrier(2)

    def attempt():
        local_session = sessionmaker(bind=engine)()
        try:
            barrier.wait(timeout=10)
            rotate_refresh_session(local_session, token)
            outcomes.append("success")
        except RefreshReuseError:
            outcomes.append("reuse")
        finally:
            local_session.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert sorted(outcomes) == ["reuse", "success"]
    with Session() as session:
        status = session.execute(text(
            "SELECT status FROM auth_refresh_families WHERE user_id=:id"
        ), {"id": user_id}).scalar_one()
        assert status == "revoked"  # the reuse loser revoked the family
        generations = session.execute(text(
            "SELECT generation FROM auth_refresh_tokens WHERE family_id IN "
            "(SELECT id FROM auth_refresh_families WHERE user_id=:id) ORDER BY generation"
        ), {"id": user_id}).scalars().all()
        assert generations == [0, 1]


def test_zz_revoke_refresh_families_and_expired_family(refresh_db):
    from app.services.auth_refresh import (
        RefreshRevokedError,
        create_refresh_session,
        revoke_refresh_families,
        rotate_refresh_session,
    )

    Session, _ = refresh_db
    user_id = _add_user(Session, "revoke-" + uuid.uuid4().hex[:8])
    with Session() as session:
        token_a = create_refresh_session(session, user_id)
        token_b = create_refresh_session(session, user_id)
        revoke_refresh_families(session, user_id)
        statuses = session.execute(text(
            "SELECT status FROM auth_refresh_families WHERE user_id=:id ORDER BY expires_at"
        ), {"id": user_id}).scalars().all()
        assert statuses == ["revoked", "revoked"]
        for stale in (token_a, token_b):
            with pytest.raises(RefreshRevokedError):
                rotate_refresh_session(session, stale)

    expired_user = _add_user(Session, "expired-" + uuid.uuid4().hex[:8])
    with Session() as session:
        token = create_refresh_session(session, expired_user)
        session.execute(text(
            "UPDATE auth_refresh_families SET expires_at = now() - interval '1 minute' "
            "WHERE user_id=:id"
        ), {"id": expired_user})
        session.commit()
        with pytest.raises(RefreshRevokedError):
            rotate_refresh_session(session, token)


# ── Router contract: cookies, CSRF/origin, logout, password revocation ───────

@pytest.fixture
def pg_client(refresh_db):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.deps import get_db

    Session, _ = refresh_db

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


def _login(client, username, password="secret123"):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


def _extract_cookie(set_cookie_headers, name):
    for header in set_cookie_headers:
        if header.startswith(f"{name}="):
            return header[len(name) + 1:].split(";")[0]
    return None


def test_router_login_sets_secure_http_only_refresh_and_csrf_cookies(pg_client, refresh_db):
    Session, _ = refresh_db
    username = "cookie-" + uuid.uuid4().hex[:8]
    _add_user(Session, username)
    response = _login(pg_client, username)
    set_cookie_headers = response.headers.get_list("set-cookie")
    refresh_cookie = next(header for header in set_cookie_headers if header.startswith(f"{REFRESH_COOKIE}="))
    assert "HttpOnly" in refresh_cookie
    assert "Secure" in refresh_cookie
    assert "samesite=strict" in refresh_cookie.lower()
    assert "Path=/api/v1/auth" in refresh_cookie
    csrf_cookie = next(header for header in set_cookie_headers if header.startswith(f"{CSRF_COOKIE}="))
    assert "HttpOnly" not in csrf_cookie  # readable by JS for the double-submit header


def test_router_refresh_rotates_with_csrf_and_origin_check(pg_client, refresh_db):
    Session, _ = refresh_db
    username = "refresh-" + uuid.uuid4().hex[:8]
    _add_user(Session, username)
    login = _login(pg_client, username)
    refresh_cookie_value = _extract_cookie(login.headers.get_list("set-cookie"), REFRESH_COOKIE)
    csrf_value = _extract_cookie(login.headers.get_list("set-cookie"), CSRF_COOKIE)

    denied = pg_client.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": "wrong"},
        cookies={REFRESH_COOKIE: refresh_cookie_value, CSRF_COOKIE: csrf_value},
    )
    assert denied.status_code == 403
    assert "CSRF_INVALID" in denied.text

    wrong_origin = pg_client.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": csrf_value, "Origin": "http://evil.example"},
        cookies={REFRESH_COOKIE: refresh_cookie_value, CSRF_COOKIE: csrf_value},
    )
    assert wrong_origin.status_code == 403
    assert "CSRF_INVALID" in wrong_origin.text

    rotated = pg_client.post(
        "/api/v1/auth/refresh",
        headers={"X-CSRF-Token": csrf_value},
        cookies={REFRESH_COOKIE: refresh_cookie_value, CSRF_COOKIE: csrf_value},
    )
    assert rotated.status_code == 200
    assert "access_token" in rotated.json()["data"]
    new_cookie = _extract_cookie(rotated.headers.get_list("set-cookie"), REFRESH_COOKIE)
    assert new_cookie and new_cookie != refresh_cookie_value


def test_router_logout_revokes_family_and_clears_cookies(pg_client, refresh_db):
    Session, _ = refresh_db
    username = "logout-" + uuid.uuid4().hex[:8]
    _add_user(Session, username)
    login = _login(pg_client, username)
    refresh_cookie_value = _extract_cookie(login.headers.get_list("set-cookie"), REFRESH_COOKIE)
    csrf_value = _extract_cookie(login.headers.get_list("set-cookie"), CSRF_COOKIE)

    logout = pg_client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_value},
        cookies={REFRESH_COOKIE: refresh_cookie_value, CSRF_COOKIE: csrf_value},
    )
    assert logout.status_code == 200
    cleared = [header for header in logout.headers.get_list("set-cookie")
               if header.startswith("ontexus_refresh=") and "Max-Age=0" in header]
    assert cleared
    with Session() as session:
        user_id = session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).scalar_one()
        assert session.execute(text(
            "SELECT status FROM auth_refresh_families WHERE user_id=:id"
        ), {"id": user_id}).scalar_one() == "revoked"


def test_router_change_password_revokes_every_family(pg_client, refresh_db):
    Session, _ = refresh_db
    username = "password-" + uuid.uuid4().hex[:8]
    _add_user(Session, username)
    login = _login(pg_client, username)
    access_token = login.json()["data"]["access_token"]
    changed = pg_client.put(
        "/api/v1/auth/password",
        json={"current_password": "secret123", "new_password": "newpass456"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert changed.status_code == 200
    with Session() as session:
        user_id = session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).scalar_one()
        assert session.execute(text(
            "SELECT count(*) FROM auth_refresh_families WHERE user_id=:id AND status='active'"
        ), {"id": user_id}).scalar_one() == 0
