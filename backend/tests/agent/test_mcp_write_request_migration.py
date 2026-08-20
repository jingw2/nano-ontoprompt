"""Migration-level contract for mcp_write_requests (Task 1 of the
ontology-mcp-server plan)."""
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
def mcp_schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "mcp_wr_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_upgrade_creates_table(mcp_schema):
    result = _alembic(mcp_schema, "upgrade", "0017_mcp_write_requests")
    assert result.returncode == 0, result.stderr
    engine = create_engine(_scoped_url(mcp_schema))
    tables = set(inspect(engine).get_table_names(schema=mcp_schema))
    engine.dispose()
    assert "mcp_write_requests" in tables


def test_downgrade_drops_table(mcp_schema):
    _alembic(mcp_schema, "upgrade", "0017_mcp_write_requests")
    result = _alembic(mcp_schema, "downgrade", "0016_oauth_pkce")
    assert result.returncode == 0, result.stderr
    engine = create_engine(_scoped_url(mcp_schema))
    tables = set(inspect(engine).get_table_names(schema=mcp_schema))
    engine.dispose()
    assert "mcp_write_requests" not in tables


def test_status_check_constraint_rejects_invalid_value(mcp_schema):
    _alembic(mcp_schema, "upgrade", "0017_mcp_write_requests")
    engine = create_engine(_scoped_url(mcp_schema))
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, 'mcpwrmig', 'mcpwrmig@example.com', 'x', 'viewer', true, now(), now(), "
            "'00000000-0000-0000-0000-000000000001')"
        ), {"id": str(uuid.uuid4())})
        user_id = conn.execute(text("SELECT id FROM users WHERE username='mcpwrmig'")).scalar_one()
        conn.execute(text(
            "INSERT INTO oauth_clients (id, client_name, redirect_uris, allowed_scopes, created_by) "
            "VALUES (:id, 'test', '[]'::json, '[]'::json, :uid)"
        ), {"id": str(uuid.uuid4()), "uid": user_id})
        client_id = conn.execute(text("SELECT id FROM oauth_clients WHERE client_name='test'")).scalar_one()
        ontology_id = str(uuid.uuid4())
        conn.execute(text(
            "INSERT INTO ontology_projects (id, name, domain, version, status, created_by, security_domain_id, created_at, updated_at) "
            "VALUES (:id, 'p', 'd', 'v0.1', 'draft', :uid, '00000000-0000-0000-0000-000000000001', now(), now())"
        ), {"id": ontology_id, "uid": user_id})
        release_id = str(uuid.uuid4())
        conn.execute(text(
            "INSERT INTO ontology_releases (id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, created_by, created_at) "
            "VALUES (:id, :oid, 1, 'v1', '{}'::bytea, '{}'::jsonb, digest('{}'::bytea,'sha256'), :uid, now())"
        ), {"id": release_id, "oid": ontology_id, "uid": user_id})
        with pytest.raises(Exception):
            conn.execute(text(
                "INSERT INTO mcp_write_requests "
                "(id, oauth_client_id, user_id, ontology_id, release_id, descriptor_id, parameters, "
                "preview_hash, preview_canonical, status, expires_at) "
                "VALUES (:id, :cid, :uid, :oid, :rid, 'action:x', '{}'::json, 'h', 'c', 'bogus', now())"
            ), {"id": str(uuid.uuid4()), "cid": client_id, "uid": user_id, "oid": ontology_id, "rid": release_id})
    engine.dispose()
