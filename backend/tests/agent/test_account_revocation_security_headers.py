"""F0-SECURITY: account/domain revocation and security-header contract.

User deactivation, the user DELETE-as-soft-delete transition, and security
-domain deactivation atomically revoke every active refresh family without
physically deleting User/family/token rows.  The exported security-header
middleware factory sets the fixed CSP/Referrer/Content-Type/Permissions
headers on every response for I-BACKEND wiring.

PostgreSQL-marked tests use TEST_DATABASE_URL; SQLite never substitutes.
"""
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
USER_SECURITY = BACKEND_DIR / "app" / "services" / "user_security.py"
MIDDLEWARE = BACKEND_DIR / "app" / "middleware" / "security_headers.py"
DOMAINS_ROUTER = BACKEND_DIR / "app" / "routers" / "security_domains.py"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_f0_security_red_contract():
    failures = []
    for path in (USER_SECURITY, MIDDLEWARE, DOMAINS_ROUTER):
        if not path.exists():
            failures.append(f"{path.relative_to(BACKEND_DIR)} missing")
    users_router = (BACKEND_DIR / "app" / "routers" / "users.py").read_text()
    for marker in ("deactivate", "soft_delete_user"):
        if marker not in users_router:
            failures.append(f"users router missing {marker}")
    user_security = USER_SECURITY.read_text()
    for marker in ("revoke_refresh_families", "soft_delete_user", "deactivate_domain"):
        if marker not in user_security:
            failures.append(f"user_security missing {marker}")
    if failures:
        pytest.fail("RED_F0_SECURITY: " + "; ".join(failures))


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
def security_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "f0_security_" + uuid.uuid4().hex
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


def _user_with_family(Session, username, role="viewer"):
    from app.services.auth_refresh import create_refresh_session
    from app.services.auth_service import hash_password

    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :username, :email, :password_hash, :role, true, now(), now(), :domain)"
        ), {
            "id": str(uuid.uuid4()), "username": username, "email": f"{username}@example.com",
            "password_hash": hash_password("secret123"), "role": role, "domain": DEFAULT_DOMAIN,
        })
        user_id = session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).scalar_one()
        session.commit()
    with Session() as session:
        token = create_refresh_session(session, user_id)
    return user_id, token


# ── middleware factory (DB-free) ─────────────────────────────────────────────

def test_security_header_middleware_sets_fixed_headers_on_every_response():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.middleware.security_headers import (
        SECURITY_HEADERS,
        create_security_headers_middleware,
    )

    assert SECURITY_HEADERS["Content-Security-Policy"] == (
        "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'self'; connect-src 'self'"
    )
    assert "unsafe-inline" not in SECURITY_HEADERS["Content-Security-Policy"]
    app = FastAPI()
    app.add_middleware(create_security_headers_middleware())

    @app.get("/probe")
    def probe():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/probe")
        assert response.status_code == 200
        for header, value in SECURITY_HEADERS.items():
            assert response.headers.get(header) == value


# ── PostgreSQL: revocation semantics ─────────────────────────────────────────

def test_zz_deactivate_user_revokes_families_and_retains_rows(security_db):
    from app.services.user_security import deactivate_user

    Session, _ = security_db
    user_id, _ = _user_with_family(Session, "deactivate-" + uuid.uuid4().hex[:8])
    with Session() as session:
        deactivate_user(session, user_id, actor_id=user_id)
    with Session() as session:
        assert session.execute(text(
            "SELECT is_active FROM users WHERE id=:id"
        ), {"id": user_id}).scalar_one() is False
        assert session.execute(text(
            "SELECT count(*) FROM auth_refresh_families WHERE user_id=:id AND status='active'"
        ), {"id": user_id}).scalar_one() == 0
        assert session.execute(text(
            "SELECT count(*) FROM auth_refresh_families WHERE user_id=:id"
        ), {"id": user_id}).scalar_one() == 1  # retained, revoked
        assert session.execute(text(
            "SELECT count(*) FROM auth_refresh_tokens WHERE family_id IN "
            "(SELECT id FROM auth_refresh_families WHERE user_id=:id)"
        ), {"id": user_id}).scalar_one() == 1  # append-only evidence retained
        assert session.execute(text(
            "SELECT count(*) FROM users WHERE id=:id"
        ), {"id": user_id}).scalar_one() == 1  # never physically deleted


def test_zz_soft_delete_user_returns_success_contract_without_deleting(security_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.routers import users as users_router
    from app.services.auth_service import create_access_token

    Session, _ = security_db
    user_id, _ = _user_with_family(Session, "softdel-" + uuid.uuid4().hex[:8])
    admin_id, _ = _user_with_family(Session, "softdel-admin-" + uuid.uuid4().hex[:8], role="admin")
    admin_token = create_access_token({"sub": admin_id, "role": "admin"})

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(users_router.router, prefix="/api/v1/users")
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        deleted = client.delete(
            f"/api/v1/users/{user_id}", headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert deleted.status_code == 204
        fetched = client.get(
            f"/api/v1/users/{user_id}", headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert fetched.status_code == 200  # row retained
        assert fetched.json()["data"]["is_active"] is False
    app.dependency_overrides.clear()
    with Session() as session:
        assert session.execute(text(
            "SELECT count(*) FROM users WHERE id=:id"
        ), {"id": user_id}).scalar_one() == 1


def test_zz_domain_deactivate_revokes_every_family_in_domain(security_db):
    from app.services.user_security import deactivate_domain

    Session, _ = security_db
    first, _ = _user_with_family(Session, "domain-a-" + uuid.uuid4().hex[:8])
    second, _ = _user_with_family(Session, "domain-b-" + uuid.uuid4().hex[:8])
    with Session() as session:
        before = session.execute(text(
            "SELECT count(*) FROM auth_refresh_families WHERE security_domain_id=:d AND status='active'"
        ), {"d": DEFAULT_DOMAIN}).scalar_one()
    with Session() as session:
        receipt = deactivate_domain(session, DEFAULT_DOMAIN, actor_id=first)
        assert receipt["revoked_families"] == before
    with Session() as session:
        assert session.execute(text(
            "SELECT count(*) FROM auth_refresh_families WHERE security_domain_id=:d AND status='active'"
        ), {"d": DEFAULT_DOMAIN}).scalar_one() == 0
        assert session.execute(text(
            "SELECT count(*) FROM security_domains WHERE id=:d"
        ), {"d": DEFAULT_DOMAIN}).scalar_one() == 1  # revoke-without-delete


# ── SQLite user-route regressions (owned tests/test_users.py) ────────────────
