"""Migration-level contract for the OAuth PKCE schema (Task 1 of the
oauth-pkce-authorization-server plan)."""
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


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


@pytest.fixture
def oauth_schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "oauth_pkce_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_upgrade_creates_all_four_tables(oauth_schema):
    result = _alembic(oauth_schema, "upgrade", "0016_oauth_pkce")
    assert result.returncode == 0, result.stderr
    engine = create_engine(_scoped_url(oauth_schema))
    tables = set(inspect(engine).get_table_names(schema=oauth_schema))
    engine.dispose()
    for name in ("oauth_clients", "oauth_authorization_codes", "oauth_refresh_families", "oauth_refresh_tokens"):
        assert name in tables


def test_downgrade_drops_all_four_tables(oauth_schema):
    _alembic(oauth_schema, "upgrade", "0016_oauth_pkce")
    result = _alembic(oauth_schema, "downgrade", "0015_external_mcp")
    assert result.returncode == 0, result.stderr
    engine = create_engine(_scoped_url(oauth_schema))
    tables = set(inspect(engine).get_table_names(schema=oauth_schema))
    engine.dispose()
    for name in ("oauth_clients", "oauth_authorization_codes", "oauth_refresh_families", "oauth_refresh_tokens"):
        assert name not in tables


def test_code_challenge_method_check_constraint_rejects_plain(oauth_schema):
    _alembic(oauth_schema, "upgrade", "0016_oauth_pkce")
    engine = create_engine(_scoped_url(oauth_schema))
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, 'oauthmig', 'oauthmig@example.com', 'x', 'viewer', true, now(), now(), "
            "'00000000-0000-0000-0000-000000000001')"
        ), {"id": str(uuid.uuid4())})
        user_id = conn.execute(text("SELECT id FROM users WHERE username='oauthmig'")).scalar_one()
        client_id = str(uuid.uuid4())
        conn.execute(text(
            "INSERT INTO oauth_clients (id, client_name, redirect_uris, allowed_scopes, created_by) "
            "VALUES (:id, 'test', '[]'::json, '[]'::json, :uid)"
        ), {"id": client_id, "uid": user_id})
        with pytest.raises(Exception):
            conn.execute(text(
                "INSERT INTO oauth_authorization_codes "
                "(id, code_hash, client_id, user_id, redirect_uri, code_challenge, code_challenge_method, expires_at) "
                "VALUES (:id, 'h', :cid, :uid, 'https://x/cb', 'c', 'plain', now())"
            ), {"id": str(uuid.uuid4()), "cid": client_id, "uid": user_id})
    engine.dispose()
