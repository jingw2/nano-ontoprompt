"""P7D: external-MCP schema-pin and OAuth-token tables migration."""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR, env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p7d_mig_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0015_external_mcp").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_0015_creates_mcp_tables_with_constraints(schema):
    engine = create_engine(_scoped_url(schema))
    inspector = inspect(engine)
    tables = set(inspector.get_table_names(schema=schema))
    for table in ("mcp_connection_schemas", "mcp_oauth_tokens"):
        assert table in tables, table
    with engine.connect() as conn:
        with pytest.raises(Exception):
            conn.execute(text(
                "INSERT INTO mcp_oauth_tokens (id, connection_version_id, encrypted_access_token, "
                "expires_at, issued_by) VALUES ('t-x', 'not-a-real-version', 'enc', now(), "
                "(SELECT id FROM users LIMIT 1))"
            ))
            conn.commit()
    engine.dispose()


def test_0015_downgrade_drops_mcp_tables(schema):
    result = _alembic(schema, "downgrade", "0014_signed_skills")
    assert result.returncode == 0, result.stderr
    engine = create_engine(_scoped_url(schema))
    inspector = inspect(engine)
    tables = set(inspector.get_table_names(schema=schema))
    for table in ("mcp_connection_schemas", "mcp_oauth_tokens"):
        assert table not in tables, table
    engine.dispose()
