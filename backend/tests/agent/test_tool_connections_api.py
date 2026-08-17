"""P7A: tool provider/connection/version admin API."""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

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
def session():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p7a_conn_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0013_external_tool_alias_unique").returncode == 0
    s = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','a','a@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.commit()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_create_provider_rejects_unknown_kind(session):
    from app.services.tool_connections import create_provider, ToolConnectionError
    with pytest.raises(ToolConnectionError):
        create_provider(session, actor_id="u-1", name="X", kind="not_a_kind")


def test_full_provider_connection_version_activation_flow(session):
    from app.services.tool_connections import (
        activate_connection_version, approve_connection_version, create_connection,
        create_connection_version, create_provider,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(
        session, actor_id="u-1", connection_id=connection["id"],
        endpoint="https://search.example.com/v1", scopes=["search:read"])
    assert version["approval_status"] == "pending"
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    activated = activate_connection_version(
        session, actor_id="u-1", connection_id=connection["id"], version_id=version["id"])
    assert activated["active_version_id"] == version["id"]


def test_activate_rejects_unapproved_version(session):
    from app.services.tool_connections import (
        create_connection, create_connection_version, create_provider, activate_connection_version,
        ToolConnectionError,
    )
    provider = create_provider(session, actor_id="u-1", name="Web Search", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"])
    with pytest.raises(ToolConnectionError):
        activate_connection_version(
            session, actor_id="u-1", connection_id=connection["id"], version_id=version["id"])
