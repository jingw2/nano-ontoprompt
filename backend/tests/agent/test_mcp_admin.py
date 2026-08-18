# backend/tests/agent/test_mcp_admin.py
"""P7D: MCP schema pinning + token issuance (service layer)."""
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
    schema = "p7d_admin_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0015_external_mcp").returncode == 0
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


def _approved_mcp_version(session, *, domains=("mcp.example.com",)) -> str:
    from app.services.tool_connections import approve_connection_version, create_connection, create_connection_version, create_provider
    provider = create_provider(session, actor_id="u-1", name="mcp-provider", kind="external_mcp")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(
        session, actor_id="u-1", connection_id=connection["id"], endpoint="https://mcp.example.com/rpc",
        allowlists={"domains": list(domains)})
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    return version["id"]


def test_pin_schema_rejects_missing_allowlist(session):
    from app.services.tool_connections import ToolConnectionError, pin_mcp_schema
    version_id = _approved_mcp_version(session, domains=())
    with pytest.raises(ToolConnectionError, match="MCP_DOMAIN_ALLOWLIST_MISSING"):
        pin_mcp_schema(session, actor_id="u-1", version_id=version_id)


def test_pin_schema_stores_hash_and_tools(session, monkeypatch):
    from app.services.tool_connections import pin_mcp_schema
    version_id = _approved_mcp_version(session)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: [
        {"name": "read_suppliers", "description": "d", "input_schema": {}}])
    result = pin_mcp_schema(session, actor_id="u-1", version_id=version_id)
    assert result["tool_count"] == 1
    stored = session.execute(text(
        "SELECT tool_schema_hash, quarantined FROM mcp_connection_schemas WHERE connection_version_id = :id"
    ), {"id": version_id}).mappings().one()
    assert stored["tool_schema_hash"] == result["tool_schema_hash"]
    assert stored["quarantined"] is False


def test_repin_clears_prior_quarantine(session, monkeypatch):
    from app.services.tool_connections import pin_mcp_schema
    version_id = _approved_mcp_version(session)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: [
        {"name": "read_suppliers", "description": "d", "input_schema": {}}])
    pin_mcp_schema(session, actor_id="u-1", version_id=version_id)
    session.execute(text(
        "UPDATE mcp_connection_schemas SET quarantined = true, quarantined_reason = 'x' "
        "WHERE connection_version_id = :id"
    ), {"id": version_id})
    session.commit()
    pin_mcp_schema(session, actor_id="u-1", version_id=version_id)
    stored = session.execute(text(
        "SELECT quarantined FROM mcp_connection_schemas WHERE connection_version_id = :id"
    ), {"id": version_id}).mappings().one()
    assert stored["quarantined"] is False


def test_issue_token_encrypts_at_rest(session):
    from app.services.encryption_service import decrypt
    from app.services.tool_connections import issue_mcp_token
    version_id = _approved_mcp_version(session)
    issue_mcp_token(session, actor_id="u-1", version_id=version_id, access_token="secret-tok",
                    refresh_token=None, expires_in_seconds=3600, scope=["ontology:query"], audience="agent-1")
    stored = session.execute(text(
        "SELECT encrypted_access_token FROM mcp_oauth_tokens WHERE connection_version_id = :id"
    ), {"id": version_id}).mappings().one()
    assert stored["encrypted_access_token"] != "secret-tok"
    assert decrypt(stored["encrypted_access_token"]) == "secret-tok"


def test_issue_token_rejects_non_mcp_kind(session):
    from app.services.tool_connections import ToolConnectionError, approve_connection_version, create_connection, create_connection_version, create_provider, issue_mcp_token
    provider = create_provider(session, actor_id="u-1", name="search-provider", kind="search")
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://search.example.com")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    with pytest.raises(ToolConnectionError, match="MCP_KIND_REQUIRED"):
        issue_mcp_token(session, actor_id="u-1", version_id=version["id"], access_token="t",
                        refresh_token=None, expires_in_seconds=3600, scope=[], audience=None)


# --- test_connection_version's external_mcp health-probe branch ---
# Ordering matters: schema pinned? -> quarantined (skip network)? -> token
# present? -> domain allowlist present? -> token expired? -> only then does
# the probe make a live list_tools call and compare hashes.

_TOOLS_A = [{"name": "read_suppliers", "description": "d", "input_schema": {}}]
_TOOLS_B = [{"name": "different_tool", "description": "d2", "input_schema": {}}]


def test_probe_unpinned_schema_unhealthy(session):
    from app.services.tool_connections import test_connection_version
    version_id = _approved_mcp_version(session)
    result = test_connection_version(session, version_id=version_id)
    assert result == {"status": "unhealthy", "detail": "MCP_SCHEMA_UNPINNED"}


def test_probe_quarantined_schema_skips_network(session, monkeypatch):
    from app.services.tool_connections import pin_mcp_schema, test_connection_version
    version_id = _approved_mcp_version(session)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: _TOOLS_A)
    pin_mcp_schema(session, actor_id="u-1", version_id=version_id)
    session.execute(text(
        "UPDATE mcp_connection_schemas SET quarantined = true, quarantined_reason = 'x' "
        "WHERE connection_version_id = :id"
    ), {"id": version_id})
    session.commit()

    calls = []

    def _tracking_list_tools(**kw):
        calls.append(kw)
        raise AssertionError("list_tools must not be called when the schema is already quarantined")

    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", _tracking_list_tools)
    result = test_connection_version(session, version_id=version_id)
    assert result == {"status": "unhealthy", "detail": "TOOL_SCHEMA_QUARANTINED"}
    assert calls == []


def test_probe_missing_token_unhealthy(session, monkeypatch):
    from app.services.tool_connections import pin_mcp_schema, test_connection_version
    version_id = _approved_mcp_version(session)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: _TOOLS_A)
    pin_mcp_schema(session, actor_id="u-1", version_id=version_id)
    result = test_connection_version(session, version_id=version_id)
    assert result == {"status": "unhealthy", "detail": "MCP_TOKEN_MISSING"}


def test_probe_expired_token_unhealthy(session, monkeypatch):
    from app.services.tool_connections import issue_mcp_token, pin_mcp_schema, test_connection_version
    version_id = _approved_mcp_version(session)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: _TOOLS_A)
    pin_mcp_schema(session, actor_id="u-1", version_id=version_id)
    issue_mcp_token(session, actor_id="u-1", version_id=version_id, access_token="tok",
                    refresh_token=None, expires_in_seconds=-10, scope=[], audience=None)
    result = test_connection_version(session, version_id=version_id)
    assert result == {"status": "unhealthy", "detail": "MCP_TOKEN_EXPIRED"}


def test_probe_healthy_when_live_hash_matches_pinned(session, monkeypatch):
    from app.services.tool_connections import issue_mcp_token, pin_mcp_schema, test_connection_version
    version_id = _approved_mcp_version(session)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: _TOOLS_A)
    pin_mcp_schema(session, actor_id="u-1", version_id=version_id)
    issue_mcp_token(session, actor_id="u-1", version_id=version_id, access_token="tok",
                    refresh_token=None, expires_in_seconds=3600, scope=[], audience=None)
    result = test_connection_version(session, version_id=version_id)
    assert result == {"status": "healthy", "detail": "mcp:ok"}
    persisted = session.execute(text(
        "SELECT health_status FROM tool_connection_versions WHERE id = :id"
    ), {"id": version_id}).scalar_one()
    assert persisted == "healthy"


def test_probe_hash_mismatch_unhealthy_and_does_not_persist_quarantine(session, monkeypatch):
    """A live-probe hash mismatch reports unhealthy but is advisory only —
    only the later Tool Gateway dispatch path is allowed to persist
    quarantine=true to mcp_connection_schemas."""
    from app.services.tool_connections import issue_mcp_token, pin_mcp_schema, test_connection_version
    version_id = _approved_mcp_version(session)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: _TOOLS_A)
    pin_mcp_schema(session, actor_id="u-1", version_id=version_id)
    issue_mcp_token(session, actor_id="u-1", version_id=version_id, access_token="tok",
                    refresh_token=None, expires_in_seconds=3600, scope=[], audience=None)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: _TOOLS_B)
    result = test_connection_version(session, version_id=version_id)
    assert result == {"status": "unhealthy", "detail": "TOOL_SCHEMA_QUARANTINED"}
    stored = session.execute(text(
        "SELECT quarantined FROM mcp_connection_schemas WHERE connection_version_id = :id"
    ), {"id": version_id}).mappings().one()
    assert stored["quarantined"] is False
