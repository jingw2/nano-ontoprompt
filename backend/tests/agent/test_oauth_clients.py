"""Task 2: admin-registered OAuth client registry (no dynamic registration)."""
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
def oauth_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "oauth_clients_" + uuid.uuid4().hex
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


def _add_user(Session, username):
    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :username, :email, 'x', 'admin', true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "username": username, "email": f"{username}@example.com", "domain": DEFAULT_DOMAIN})
        session.commit()
        return session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).scalar_one()


def test_create_get_list_deactivate(oauth_db):
    from app.services import oauth_clients

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    with Session() as session:
        client = oauth_clients.create_client(
            session, client_name="Test MCP Client", redirect_uris=["https://client.example/cb"],
            allowed_scopes=["ontology:read", "ontology:write"], created_by=admin_id,
        )
        assert client.id and client.is_active is True

        fetched = oauth_clients.get_client(session, client.id)
        assert fetched is not None and fetched.client_name == "Test MCP Client"

        assert oauth_clients.get_client(session, "nonexistent") is None

        all_clients = oauth_clients.list_clients(session)
        assert any(c.id == client.id for c in all_clients)

        oauth_clients.deactivate_client(session, client.id)
        assert oauth_clients.get_client(session, client.id).is_active is False


def test_validate_redirect_uri_exact_match_only(oauth_db):
    from app.services import oauth_clients

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    with Session() as session:
        client = oauth_clients.create_client(
            session, client_name="X", redirect_uris=["https://client.example/cb"],
            allowed_scopes=[], created_by=admin_id,
        )
        assert oauth_clients.validate_redirect_uri(client, "https://client.example/cb") is True
        # not a prefix match, not a substring match, not a trailing-slash match
        assert oauth_clients.validate_redirect_uri(client, "https://client.example/cb/extra") is False
        assert oauth_clients.validate_redirect_uri(client, "https://client.example/cb/") is False
        assert oauth_clients.validate_redirect_uri(client, "https://evil.example/cb") is False


def test_resolve_scope_intersection_and_rejection(oauth_db):
    from app.services import oauth_clients

    Session = oauth_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8])
    with Session() as session:
        client = oauth_clients.create_client(
            session, client_name="X", redirect_uris=[], allowed_scopes=["a", "b"], created_by=admin_id,
        )
        assert oauth_clients.resolve_scope(client, "a") == "a"
        assert oauth_clients.resolve_scope(client, "b a") == "a b"
        assert oauth_clients.resolve_scope(client, None) == "a b"
        assert oauth_clients.resolve_scope(client, "") == "a b"
        with pytest.raises(ValueError):
            oauth_clients.resolve_scope(client, "a c")
