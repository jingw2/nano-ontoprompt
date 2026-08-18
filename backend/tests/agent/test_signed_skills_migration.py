"""P7C: signed-skill tables migration."""
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
    schema = "p7c_mig_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0014_signed_skills").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_0014_creates_skill_tables_with_constraints(schema):
    engine = create_engine(_scoped_url(schema))
    inspector = inspect(engine)
    tables = set(inspector.get_table_names(schema=schema))
    for table in ("skill_packages", "skill_versions", "skill_signatures", "agent_skill_bindings"):
        assert table in tables, table
    with engine.connect() as conn:
        with pytest.raises(Exception):
            conn.execute(text(
                "INSERT INTO skill_versions (id, package_id, version_no, manifest, canonical_hash, "
                "approval_status, created_by, created_at) "
                "VALUES ('v-x', 'p-x', 1, '{}'::json, 'h', 'not_a_status', "
                "(SELECT id FROM users LIMIT 1), now())"
            ))
            conn.commit()
    engine.dispose()


def test_0014_downgrade_drops_skill_tables(schema):
    result = _alembic(schema, "downgrade", "0013_external_tool_alias_unique")
    assert result.returncode == 0, result.stderr
    engine = create_engine(_scoped_url(schema))
    inspector = inspect(engine)
    tables = set(inspector.get_table_names(schema=schema))
    for table in ("skill_packages", "skill_versions", "skill_signatures", "agent_skill_bindings"):
        assert table not in tables, table
    engine.dispose()
