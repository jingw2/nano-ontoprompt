"""P3B-INTERRUPT: Agent clarification interrupts.

Persisted ClarificationRequest before interrupt; answer CAS
`(pending, base_request_revision) -> answered`, user message, and unique
resume dispatch outbox in one transaction.  Double answer ->
CLARIFICATION_ALREADY_RESOLVED; changed base revision -> CLARIFICATION_STALE.
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

from app.routers.agent_clarifications import router
from app.services.auth_service import create_access_token


BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p3b_interrupt_red_contract():
    failures = []
    for path in ("app/services/runtime/clarification.py", "app/routers/agent_clarifications.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    svc = BACKEND_DIR / "app" / "services" / "runtime" / "clarification.py"
    if svc.exists():
        for symbol in ("create_clarification", "get_clarification", "answer_clarification"):
            if symbol not in svc.read_text():
                failures.append(f"clarification.py missing {symbol}")
    if failures:
        pytest.fail("RED_P3B_INTERRUPT: " + "; ".join(failures))


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
    schema = "p3b_clarify_" + uuid.uuid4().hex
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


def _client(session):
    from app.deps import get_db

    def override_get_db():
        yield session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


def _seed(session, *, editor_id="u-1", agent_id="a-1", turn_id="t-1"):
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:u,'s','s@t.com','h','editor',true,:d,now(),now())"
    ), {"u": editor_id, "d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-1','O','test','v1','created',:u,now(),now(),:d,1)"
    ), {"u": editor_id, "d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES (:id,'private','active',:u,now(),now())"
    ), {"id": agent_id, "u": editor_id})
    session.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, created_by, created_at, updated_at) "
        "VALUES (:id, :agent, :u, CAST(:caps AS json), 1, 'active', :u, now(), now())"
    ), {"id": str(uuid.uuid4()), "agent": agent_id, "u": editor_id,
        "caps": '["discover", "run", "view_config", "edit", "view_audit"]'})
    session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) "
        "VALUES ('s-1', :agent, :u, 'active')"
    ), {"agent": agent_id, "u": editor_id})
    session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
        "VALUES (:tid, 's-1', 'running', now(), now())"
    ), {"tid": turn_id})
    session.commit()


def test_clarification_answer_resumes_once(schema):
    session = _session(schema)
    _seed(session)
    from app.services.runtime.clarification import create_clarification
    created = create_clarification(session, turn_id="t-1", question="Which account?")
    clarification_id = created["id"]
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # detail -> 200
            r = c.get(f"/api/v1/agent-clarifications/{clarification_id}", headers=headers)
            assert r.status_code == 200
            assert r.json()["data"]["status"] == "pending"
            assert r.json()["data"]["question"] == "Which account?"
            # answer -> 202 with resume outbox
            r = c.post(f"/api/v1/agent-clarifications/{clarification_id}/answer",
                       json={"base_request_revision": 1, "answer": "Account 42"},
                       headers=headers)
            assert r.status_code == 202
            data = r.json()["data"]
            assert data["status"] == "queued"
            assert data["dispatch_generation"] == 2
            assert data["correlation_id"].startswith("clarification:answer:")
            # state/audit/outbox rows
            assert session.execute(text(
                "SELECT status FROM agent_clarification_requests WHERE id = :id"
            ), {"id": clarification_id}).scalar_one() == "answered"
            assert session.execute(text(
                "SELECT count(*) FROM agent_messages WHERE content = 'Account 42'"
            )).scalar_one() == 1
            assert session.execute(text(
                "SELECT count(*) FROM agent_turn_dispatch_outbox "
                "WHERE turn_id = 't-1' AND operation = 'resume_clarification'"
            )).scalar_one() == 1
            # second answer -> 409 CLARIFICATION_ALREADY_RESOLVED
            r = c.post(f"/api/v1/agent-clarifications/{clarification_id}/answer",
                       json={"base_request_revision": 1, "answer": "Again"},
                       headers=headers)
            assert r.status_code == 409
            assert "CLARIFICATION_ALREADY_RESOLVED" in r.json()["detail"]
    finally:
        session.close()


def test_clarification_stale_base_revision(schema):
    session = _session(schema)
    _seed(session)
    from app.services.runtime.clarification import create_clarification
    clarification_id = create_clarification(session, turn_id="t-1", question="Q")["id"]
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            r = c.post(f"/api/v1/agent-clarifications/{clarification_id}/answer",
                       json={"base_request_revision": 99, "answer": "x"},
                       headers=headers)
            assert r.status_code == 409
            assert "CLARIFICATION_STALE" in r.json()["detail"]
    finally:
        session.close()


def test_clarification_existence_hiding(schema):
    session = _session(schema)
    _seed(session)
    stranger_id = str(uuid.uuid4())
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:id,'st','st@t.com','h','editor',true,:d,now(),now())"
    ), {"id": stranger_id, "d": DEFAULT_DOMAIN})
    session.commit()
    from app.services.runtime.clarification import create_clarification
    clarification_id = create_clarification(session, turn_id="t-1", question="Q")["id"]
    stranger_headers = {"Authorization": f"Bearer {create_access_token({'sub': stranger_id, 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            assert c.get(f"/api/v1/agent-clarifications/{clarification_id}", headers=stranger_headers).status_code == 404
            assert c.post(f"/api/v1/agent-clarifications/{clarification_id}/answer",
                          json={"base_request_revision": 1, "answer": "x"},
                          headers=stranger_headers).status_code == 404
    finally:
        session.close()
