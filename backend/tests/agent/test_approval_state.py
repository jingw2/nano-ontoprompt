"""P5B-APPROVAL: canonical Agent action approval state machine.

Only pending CASes to approved|rejected|stale; expiry CASes pending->expired;
stale terminalization fails the awaiting_approval Turn with APPROVAL_STALE,
invalidates dispatch rows and clears the session pointer with no resume.
Double click -> APPROVAL_ALREADY_RESOLVED; wrong actor -> existence-hiding
404; each terminal state is reached exactly once.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.routers.agent_approvals import router
from app.services.auth_service import create_access_token


BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p5b_approval_red_contract():
    failures = []
    for path in ("app/services/actions/approval.py", "app/routers/agent_approvals.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    svc = BACKEND_DIR / "app" / "services" / "actions" / "approval.py"
    if svc.exists():
        for symbol in ("create_approval", "resolve_approval", "sweep_expired_approvals"):
            if symbol not in svc.read_text():
                failures.append(f"approval.py missing {symbol}")
    if failures:
        pytest.fail("RED_P5B_APPROVAL: " + "; ".join(failures))


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


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
def schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p5b_approval_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0006_agent_runtime").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _session(schema):
    return sessionmaker(bind=create_engine(_scoped_url(schema)))()


def _seed(schema, *, actor_id="u-1", agent_id="a-1", turn_status="awaiting_approval"):
    s = _session(schema)
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:u,'s','s@t.com','h','editor',true,:d,now(),now())"
    ), {"u": actor_id, "d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-1','O','test','v1','created','u-1',now(),now(),:d,1)"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES (:id,'private','active',:u,now(),now())"
    ), {"id": agent_id, "u": actor_id})
    s.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, created_by, created_at, updated_at) "
        "VALUES (:id, :agent, :u, CAST(:caps AS json), 1, 'active', :u, now(), now())"
    ), {"id": str(uuid.uuid4()), "agent": agent_id, "u": actor_id,
        "caps": '["discover", "run", "view_config", "edit", "view_audit"]'})
    s.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) "
        "VALUES ('s-1', :agent, :u, 'active')"
    ), {"agent": agent_id, "u": actor_id})
    s.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
        "VALUES ('t-1', 's-1', :status, now(), now())"
    ), {"status": turn_status})
    s.execute(text(
        "UPDATE agent_sessions SET active_turn_id = 't-1' WHERE id = 's-1'"
    ))
    s.execute(text(
        "INSERT INTO agent_tool_executions (id, turn_id, idempotency_key, status, descriptor, "
        "parameters_hash, created_at, updated_at) "
        "VALUES ('te-1', 't-1', 'exec-1', 'proposed', '{}'::json, :ph, now(), now())"
    ), {"ph": "p" * 64})
    s.commit()
    s.close()


def _make_approval(schema, **overrides):
    s = _session(schema)
    from app.services.actions.approval import create_approval
    params = dict(
        turn_id="t-1", tool_execution_id="te-1", node_execution_id=None,
        designated_actor_id="u-1", preview_hash="h" * 64, parameter_hash="p" * 64,
        schema_hash="s" * 64, release_hash="r" * 64, model_hash="m" * 64, policy_hash="o" * 64,
    )
    params.update(overrides)
    created = create_approval(s, **params)
    s.close()
    return created["id"]


def _client(session):
    from app.deps import get_db

    def override_get_db():
        yield session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


def test_approve_resumes_once(schema):
    _seed(schema)
    approval_id = _make_approval(schema)
    s = _session(schema)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            # detail -> 200 pending
            r = c.get(f"/api/v1/agent-approvals/{approval_id}", headers=headers)
            assert r.status_code == 200
            assert r.json()["data"]["status"] == "pending"
            # approve -> 202 with resume outbox
            r = c.post(f"/api/v1/agent-approvals/{approval_id}/approve",
                       json={"base_revision": 1, "preview_hash": "h" * 64}, headers=headers)
            assert r.status_code == 202
            data = r.json()["data"]
            assert data["status"] == "approved"
            assert data["correlation_id"].startswith("approval:approve:")
            assert s.execute(text(
                "SELECT count(*) FROM agent_turn_dispatch_outbox WHERE operation = 'resume_approval'"
            )).scalar_one() == 1
            # double click -> 409 ALREADY_RESOLVED
            r = c.post(f"/api/v1/agent-approvals/{approval_id}/approve",
                       json={"base_revision": 2, "preview_hash": "h" * 64}, headers=headers)
            assert r.status_code == 409
            assert "APPROVAL_ALREADY_RESOLVED" in r.json()["detail"]
            # status terminal exactly once
            assert s.execute(text(
                "SELECT count(*) FROM agent_approvals WHERE status = 'approved'"
            )).scalar_one() == 1
    finally:
        s.close()


def test_reject_marks_execution_non_runnable(schema):
    _seed(schema)
    approval_id = _make_approval(schema)
    s = _session(schema)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            r = c.post(f"/api/v1/agent-approvals/{approval_id}/reject",
                       json={"base_revision": 1, "preview_hash": "h" * 64}, headers=headers)
            assert r.status_code == 202
            assert r.json()["data"]["status"] == "rejected"
            assert s.execute(text(
                "SELECT status FROM agent_tool_executions WHERE id = 'te-1'"
            )).scalar_one() == "cancelled"
            # no resume outbox on reject
            assert s.execute(text(
                "SELECT count(*) FROM agent_turn_dispatch_outbox"
            )).scalar_one() == 0
    finally:
        s.close()


def test_stale_base_revision(schema):
    _seed(schema)
    approval_id = _make_approval(schema)
    s = _session(schema)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            r = c.post(f"/api/v1/agent-approvals/{approval_id}/approve",
                       json={"base_revision": 99, "preview_hash": "h" * 64}, headers=headers)
            assert r.status_code == 409
            assert "APPROVAL_STALE" in r.json()["detail"]
    finally:
        s.close()


def test_wrong_actor_existence_hiding(schema):
    _seed(schema)
    approval_id = _make_approval(schema)
    stranger_id = str(uuid.uuid4())
    s = _session(schema)
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:id,'st','st@t.com','h','editor',true,:d,now(),now())"
    ), {"id": stranger_id, "d": DEFAULT_DOMAIN})
    s.commit()
    stranger_headers = {"Authorization": f"Bearer {create_access_token({'sub': stranger_id, 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            assert c.get(f"/api/v1/agent-approvals/{approval_id}", headers=stranger_headers).status_code == 404
            assert c.post(f"/api/v1/agent-approvals/{approval_id}/approve",
                          json={"base_revision": 1, "preview_hash": "h" * 64},
                          headers=stranger_headers).status_code == 404
    finally:
        s.close()


def test_expiry_sweeper_terminalizes(schema):
    _seed(schema)
    approval_id = _make_approval(schema)
    s = _session(schema)
    from app.services.actions.approval import sweep_expired_approvals
    s.execute(text("UPDATE agent_approvals SET expires_at = now() - interval '1 minute' WHERE id = :id"),
              {"id": approval_id})
    s.commit()
    swept = sweep_expired_approvals(s)
    assert swept == 1
    assert s.execute(text("SELECT status FROM agent_approvals WHERE id = :id"),
                     {"id": approval_id}).scalar_one() == "expired"
    s.close()
