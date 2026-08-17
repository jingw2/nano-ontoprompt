"""P6A: retention governance migration adds versioned policies, holds, and
the epoch counter without touching the immutable security_domains table."""
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
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


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
    schema = "p6a_migration_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_p6a_migration_red_contract():
    migration = BACKEND_DIR / "alembic" / "versions" / "0011_retention_governance.py"
    if not migration.exists():
        pytest.fail("RED_P6A_MIGRATION: missing alembic/versions/0011_retention_governance.py")
    source = migration.read_text()
    for symbol in ("retention_policies", "retention_policy_versions", "retention_holds", "retention_epochs"):
        if symbol not in source:
            pytest.fail(f"RED_P6A_MIGRATION: 0011 missing {symbol}")


def test_fresh_0011_creates_tables_and_backfills(schema):
    # seed via 0010 baseline (includes default security_domain from 0003)
    result_0010 = _alembic(schema, "upgrade", "0010_agent_single_binding")
    assert result_0010.returncode == 0, result_0010.stderr

    result = _alembic(schema, "upgrade", "0011_retention_governance", check=False)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    engine = create_engine(_scoped_url(schema))
    inspector = inspect(engine)
    tables = set(inspector.get_table_names(schema=schema))
    for table in ("retention_policies", "retention_policy_versions", "retention_holds", "retention_epochs"):
        assert table in tables, table

    with engine.connect() as conn:
        # get the domain that was backfilled
        domain_row = conn.execute(text(
            "SELECT security_domain_id FROM retention_epochs LIMIT 1"
        )).mappings().one()
        domain_id = domain_row["security_domain_id"]

        epoch_row = conn.execute(text(
            "SELECT epoch FROM retention_epochs WHERE security_domain_id = :d"
        ), {"d": domain_id}).mappings().one()
        assert epoch_row["epoch"] == 0

        policy = conn.execute(text(
            "SELECT p.id, p.status, p.active_version_id, v.version_no, v.rules, v.status AS v_status "
            "FROM retention_policies p JOIN retention_policy_versions v ON v.id = p.active_version_id "
            "WHERE p.security_domain_id = :d"
        ), {"d": domain_id}).mappings().one()
        assert policy["status"] == "active"
        assert policy["version_no"] == 1
        assert policy["v_status"] == "active"
        # built-in minimums are present verbatim (see Task 3 TABLE_MINIMUMS)
        assert policy["rules"]["message.redact"] == 90
        assert policy["rules"]["turn.delete"] == 7

    engine.dispose()


def test_0011_downgrade_drops_retention_tables(schema):
    assert _alembic(schema, "upgrade", "0011_retention_governance").returncode == 0
    result = _alembic(schema, "downgrade", "0010_agent_single_binding")
    assert result.returncode == 0, result.stderr
    engine = create_engine(_scoped_url(schema))
    inspector = inspect(engine)
    tables = set(inspector.get_table_names(schema=schema))
    for table in ("retention_policies", "retention_policy_versions", "retention_holds", "retention_epochs"):
        assert table not in tables, table
    engine.dispose()
