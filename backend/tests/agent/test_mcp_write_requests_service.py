"""Task 2: MCP-native write-request service — grant check, preview-based
creation, ownership-scoped resolution, lazy expiry."""
import os
from pathlib import Path
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
def mcp_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "mcp_wr_svc_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    result = _alembic(schema, "upgrade", "0017_mcp_write_requests")
    assert result.returncode == 0, result.stderr
    session_engine = create_engine(_scoped_url(schema))
    Session = sessionmaker(bind=session_engine)
    yield Session
    session_engine.dispose()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _add_user(Session, username, role="viewer"):
    with Session() as session:
        session.execute(text(
            "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at, security_domain_id) "
            "VALUES (:id, :username, :email, 'x', :role, true, now(), now(), :domain)"
        ), {"id": str(uuid.uuid4()), "username": username, "email": f"{username}@example.com", "role": role, "domain": DEFAULT_DOMAIN})
        session.commit()
        return session.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).scalar_one()


def _add_client(Session, admin_id):
    from app.services import oauth_clients
    with Session() as session:
        client = oauth_clients.create_client(
            session, client_name="X", redirect_uris=[], allowed_scopes=["ontology:read", "ontology:write"], created_by=admin_id,
        )
        return client.id


def _add_ontology_and_release(Session, created_by):
    with Session() as session:
        ontology_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO ontology_projects (id, name, domain, version, status, created_by, created_at, updated_at) "
            "VALUES (:id, 'p', 'd', 'v0.1', 'draft', :created_by, now(), now())"
        ), {"id": ontology_id, "created_by": created_by})
        release_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO ontology_releases (id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, created_by, created_at) "
            "VALUES (:id, :oid, 1, 'v1', :mb, '{}'::jsonb, digest(:mb,'sha256'), :uid, now())"
        ), {"id": release_id, "oid": ontology_id, "mb": b"{}", "uid": created_by})
        session.commit()
        return ontology_id, release_id


def _grant_write(Session, ontology_id, user_id, created_by):
    with Session() as session:
        session.execute(text(
            "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, status, created_at, revision, created_by) "
            "VALUES (:id, :o, :u, :cap, 'active', now(), 1, :created_by)"
        ), {"id": str(uuid.uuid4()), "o": ontology_id, "u": user_id, "cap": '["execute_instance_action"]', "created_by": created_by})
        session.commit()


def test_create_requires_data_grant(mcp_db):
    from app.services.mcp_write_requests import McpWriteRequestError, create_write_request

    Session = mcp_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8], role="admin")
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    ontology_id, release_id = _add_ontology_and_release(Session, admin_id)
    with Session() as session:
        with pytest.raises(McpWriteRequestError) as excinfo:
            create_write_request(
                session, oauth_client_id=client_id, user_id=user_id, ontology_id=ontology_id,
                release_id=release_id, descriptor_id="action:x", parameters={},
            )
        assert excinfo.value.code == "DATA_GRANT_DENIED"


def test_create_succeeds_with_grant_and_can_be_approved(mcp_db):
    from app.services.mcp_write_requests import (
        approve_write_request, create_write_request, get_write_request, list_pending_for_user,
    )

    Session = mcp_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8], role="admin")
    user_id = _add_user(Session, "user-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    ontology_id, release_id = _add_ontology_and_release(Session, admin_id)
    _grant_write(Session, ontology_id, user_id, admin_id)
    with Session() as session:
        result = create_write_request(
            session, oauth_client_id=client_id, user_id=user_id, ontology_id=ontology_id,
            release_id=release_id, descriptor_id="action:x", parameters={"foo": "bar"},
        )
        assert result["status"] == "pending" and result["preview_hash"]

        pending = list_pending_for_user(session, user_id=user_id)
        assert len(pending) == 1 and pending[0]["id"] == result["request_id"]

        approved = approve_write_request(session, request_id=result["request_id"], actor_id=user_id)
        assert approved["status"] == "approved"

        item = get_write_request(session, request_id=result["request_id"], user_id=user_id)
        assert item["status"] == "approved"

        # resolved requests drop out of the pending list
        assert list_pending_for_user(session, user_id=user_id) == []


def test_approve_is_ownership_scoped_and_single_use(mcp_db):
    from app.services.mcp_write_requests import McpWriteRequestError, approve_write_request, create_write_request

    Session = mcp_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8], role="admin")
    owner_id = _add_user(Session, "owner-" + uuid.uuid4().hex[:8])
    other_id = _add_user(Session, "other-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    ontology_id, release_id = _add_ontology_and_release(Session, admin_id)
    _grant_write(Session, ontology_id, owner_id, admin_id)
    with Session() as session:
        result = create_write_request(
            session, oauth_client_id=client_id, user_id=owner_id, ontology_id=ontology_id,
            release_id=release_id, descriptor_id="action:x", parameters={},
        )
        # a different user cannot approve someone else's request
        with pytest.raises(McpWriteRequestError):
            approve_write_request(session, request_id=result["request_id"], actor_id=other_id)
        # the owner can
        approve_write_request(session, request_id=result["request_id"], actor_id=owner_id)
        # approving twice fails (already resolved)
        with pytest.raises(McpWriteRequestError):
            approve_write_request(session, request_id=result["request_id"], actor_id=owner_id)


def test_get_write_request_returns_none_for_wrong_owner(mcp_db):
    from app.services.mcp_write_requests import create_write_request, get_write_request

    Session = mcp_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8], role="admin")
    owner_id = _add_user(Session, "owner-" + uuid.uuid4().hex[:8])
    other_id = _add_user(Session, "other-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    ontology_id, release_id = _add_ontology_and_release(Session, admin_id)
    _grant_write(Session, ontology_id, owner_id, admin_id)
    with Session() as session:
        result = create_write_request(
            session, oauth_client_id=client_id, user_id=owner_id, ontology_id=ontology_id,
            release_id=release_id, descriptor_id="action:x", parameters={},
        )
        assert get_write_request(session, request_id=result["request_id"], user_id=other_id) is None
        assert get_write_request(session, request_id=result["request_id"], user_id=owner_id) is not None


def test_pending_request_reports_expired_after_expiry_without_a_sweep(mcp_db):
    from app.services.mcp_write_requests import create_write_request, get_write_request
    from app.models.mcp_write_request import McpWriteRequest

    Session = mcp_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8], role="admin")
    owner_id = _add_user(Session, "owner-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    ontology_id, release_id = _add_ontology_and_release(Session, admin_id)
    _grant_write(Session, ontology_id, owner_id, admin_id)
    with Session() as session:
        result = create_write_request(
            session, oauth_client_id=client_id, user_id=owner_id, ontology_id=ontology_id,
            release_id=release_id, descriptor_id="action:x", parameters={},
        )
        session.query(McpWriteRequest).filter_by(id=result["request_id"]).update(
            {"expires_at": datetime.now(timezone.utc) - timedelta(hours=1)}
        )
        session.commit()
        item = get_write_request(session, request_id=result["request_id"], user_id=owner_id)
        assert item["status"] == "expired"


def test_expired_pending_request_cannot_be_approved_and_is_excluded_from_list(mcp_db):
    from app.services.mcp_write_requests import (
        McpWriteRequestError, approve_write_request, create_write_request, list_pending_for_user,
    )
    from app.models.mcp_write_request import McpWriteRequest

    Session = mcp_db
    admin_id = _add_user(Session, "admin-" + uuid.uuid4().hex[:8], role="admin")
    owner_id = _add_user(Session, "owner-" + uuid.uuid4().hex[:8])
    client_id = _add_client(Session, admin_id)
    ontology_id, release_id = _add_ontology_and_release(Session, admin_id)
    _grant_write(Session, ontology_id, owner_id, admin_id)
    with Session() as session:
        result = create_write_request(
            session, oauth_client_id=client_id, user_id=owner_id, ontology_id=ontology_id,
            release_id=release_id, descriptor_id="action:x", parameters={},
        )
        session.query(McpWriteRequest).filter_by(id=result["request_id"]).update(
            {"expires_at": datetime.now(timezone.utc) - timedelta(hours=1)}
        )
        session.commit()
        assert list_pending_for_user(session, user_id=owner_id) == []
        with pytest.raises(McpWriteRequestError):
            approve_write_request(session, request_id=result["request_id"], actor_id=owner_id)
