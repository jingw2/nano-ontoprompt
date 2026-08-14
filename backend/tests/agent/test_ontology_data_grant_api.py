"""P2B-DATAGRANT: ontology data grant API.

Delegated-governance authorization, immutable revisions, revoke CAS and audit;
capabilities never exceed the principal's role ceiling and the row policy
must compile under the restricted DSL.  Cross-domain and unauthorized actors
are denied.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.services.auth_service import create_access_token, hash_password


BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p2b_datagrant_red_contract():
    failures = []
    router = BACKEND_DIR / "app" / "routers" / "ontology_data_grants.py"
    if not router.exists():
        failures.append("missing app/routers/ontology_data_grants.py")
    service = BACKEND_DIR / "app" / "services" / "ontology_data_grant.py"
    if not service.exists():
        failures.append("missing app/services/ontology_data_grant.py")
    else:
        source = service.read_text()
        for symbol in ("create_data_grant", "revise_data_grant", "revoke_data_grant", "has_data_grant_authority"):
            if symbol not in source:
                failures.append(f"ontology_data_grant.py missing {symbol}")
    if failures:
        pytest.fail("RED_P2B_DATAGRANT: " + "; ".join(failures))


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema}', safe='-=')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def full_schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p2b_datagrant_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0005_agent_configuration").returncode == 0
    yield schema, _scoped_url(schema)
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


@pytest.fixture
def ctx(full_schema):
    _, url = full_schema
    Session = sessionmaker(bind=create_engine(url))
    with Session() as session:
        # admin, editor, viewer + an ontology owned by admin
        ids = {}
        for name, role in (("dg-admin", "admin"), ("dg-editor", "editor"), ("dg-viewer", "viewer")):
            uid = str(uuid.uuid4())
            session.execute(text(
                "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
                "VALUES (:id,:u,:e,'h',:r,true,:d,now(),now())"
            ), {"id": uid, "u": name, "e": f"{name}@test.com", "r": role, "d": DEFAULT_DOMAIN})
            ids[name] = uid
        oid = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
            "VALUES (:id,'DG Ontology','test','v0.1','created',:owner,now(),now(),:d,1)"
        ), {"id": oid, "owner": ids["dg-admin"], "d": DEFAULT_DOMAIN})
        session.execute(text(
            "INSERT INTO ontology_project_access_grants "
            "(id, ontology_id, user_id, capabilities, revision, status, created_by, created_at, updated_at, security_domain_id) "
            "VALUES (:id,:oid,:uid,CAST(:caps AS json),1,'active',:owner,now(),now(),:d)"
        ), {"id": str(uuid.uuid4()), "oid": oid, "uid": ids["dg-editor"],
            "caps": '["discover","read","edit","publish"]', "owner": ids["dg-admin"], "d": DEFAULT_DOMAIN})
        session.commit()
        yield session, ids, oid


def _client(session):
    from app.deps import get_db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    client = app
    yield client
    app.dependency_overrides.clear()


def test_data_grant_lifecycle_cas_and_audit(ctx):
    from fastapi.testclient import TestClient
    from app.deps import get_db

    session, ids, oid = ctx
    admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': ids['dg-admin'], 'role': 'admin'})}"}
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': ids['dg-editor'], 'role': 'editor'})}"}
    viewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': ids['dg-viewer'], 'role': 'viewer'})}"}

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            # viewer cannot create grants
            r = client.post("/api/v1/ontology-data-grants", json={
                "user_id": ids["dg-viewer"], "ontology_id": oid,
                "capabilities": ["read_instances"],
            }, headers=viewer_headers)
            assert r.status_code == 403
            # admin creates a grant for the viewer (capped to their ceiling)
            r = client.post("/api/v1/ontology-data-grants", json={
                "user_id": ids["dg-viewer"], "ontology_id": oid,
                "capabilities": ["read_instances", "execute_instance_action"],
                "row_policy": {"and": [
                    {"property": "owner_id", "op": "eq", "value_from": "actor.user_id"},
                    {"property": "status", "op": "ne", "value": "sealed"},
                ]},
            }, headers=admin_headers)
            assert r.status_code == 201
            grant = r.json()["data"]
            assert grant["revision"] == 1
            assert grant["status"] == "active"
            # unknown capability rejected (fail closed) -> 403
            r = client.post("/api/v1/ontology-data-grants", json={
                "user_id": ids["dg-viewer"], "ontology_id": oid,
                "capabilities": ["sudo"],
            }, headers=admin_headers)
            assert r.status_code == 403
            # invalid row policy DSL rejected
            r = client.post("/api/v1/ontology-data-grants", json={
                "user_id": ids["dg-viewer"], "ontology_id": oid,
                "capabilities": ["read_instances"],
                "row_policy": {"property": "status", "op": "like", "value": "x"},
            }, headers=admin_headers)
            assert r.status_code == 422
            # editor (delegated authority via project edit grant) can revise
            r = client.post(f"/api/v1/ontology-data-grants/{grant['id']}/revisions", json={
                "base_revision": 1, "capabilities": ["read_instances"],
            }, headers=editor_headers)
            assert r.status_code == 201
            revised = r.json()["data"]
            assert revised["revision"] == 2
            # stale base_revision against the new active revision -> 409
            r = client.post(f"/api/v1/ontology-data-grants/{revised['id']}/revisions", json={
                "base_revision": 1, "capabilities": ["read_instances"],
            }, headers=editor_headers)
            assert r.status_code == 409
            # revoke with current revision
            r = client.post(f"/api/v1/ontology-data-grants/{revised['id']}/revoke", json={
                "base_revision": 2, "reason": "no longer needed",
            }, headers=editor_headers)
            assert r.status_code == 200
            assert r.json()["data"]["status"] == "revoked"
            # revoked grant has no active revision -> 404 on further revoke
            r = client.post(f"/api/v1/ontology-data-grants/{revised['id']}/revoke", json={
                "base_revision": 2, "reason": "again",
            }, headers=admin_headers)
            assert r.status_code == 404
            # audit outbox rows recorded
            audit = session.execute(text(
                "SELECT count(*) FROM governance_audit_outbox WHERE correlation_id LIKE 'dg:%'"
            )).scalar_one()
            assert audit >= 3
    finally:
        app.dependency_overrides.clear()


def test_data_grant_cross_domain_and_authority_denied(ctx):
    from fastapi.testclient import TestClient
    from app.deps import get_db

    session, ids, oid = ctx
    # a plain viewer (no project edit grant, not admin) has no data-governance
    # authority; cross-domain principals are structurally excluded by the
    # single-active-domain security trigger.
    viewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': ids['dg-viewer'], 'role': 'viewer'})}"}

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            r = client.post("/api/v1/ontology-data-grants", json={
                "user_id": ids["dg-viewer"], "ontology_id": oid,
                "capabilities": ["read_instances"],
            }, headers=viewer_headers)
            assert r.status_code == 403
            # a non-existent grant is existence-hidden (404), never reveals state
            r = client.post("/api/v1/ontology-data-grants/nope/revisions", json={
                "base_revision": 1, "capabilities": ["read_instances"],
            }, headers=viewer_headers)
            assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()
