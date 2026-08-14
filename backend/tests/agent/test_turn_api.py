"""P3A-TURNAPI: durable Agent Turn commands.

Session/Turn create/status/cancel and single-use stream-ticket receipts.
Turn creation returns 202 only after the authoritative state plus the
transactional dispatch outbox commit; repeated `turn_id` replays the stored
Turn; cancel delegates to the dispatch service; tickets are fresh 60-second
single-use secrets returned once.  No graph execution or background fallback.
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

from app.routers.agent_turns import router
from app.services.auth_service import create_access_token


BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p3a_turnapi_red_contract():
    failures = []
    for path in ("app/services/runtime/turns.py", "app/routers/agent_turns.py",
                 "app/schemas/agent_runtime.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    svc = BACKEND_DIR / "app" / "services" / "runtime" / "turns.py"
    if svc.exists():
        for symbol in ("create_turn", "get_turn", "cancel_turn_api", "mint_stream_ticket",
                       "create_session", "list_messages"):
            if symbol not in svc.read_text():
                failures.append(f"turns.py missing {symbol}")
    if failures:
        pytest.fail("RED_P3A_TURNAPI: " + "; ".join(failures))


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
    schema = "p3a_turnapi_" + uuid.uuid4().hex
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


def _seed(session, *, editor_id="u-1", agent_id="a-1"):
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
    session.commit()


def test_turn_create_202_and_status(schema):
    session = _session(schema)
    _seed(session)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # create session -> 201
            r = c.post("/api/v1/agents/a-1/sessions", json={"title": "S1"}, headers=headers)
            assert r.status_code == 201
            session_id = r.json()["data"]["id"]
            # create turn -> 202 with dispatch outbox committed
            r = c.post(
                f"/api/v1/agent-sessions/{session_id}/turns",
                json={"user_message": "Hello", "turn_id": "turn-1"},
                headers={**headers, "Idempotency-Key": "ag-turn-1234567890"},
            )
            assert r.status_code == 202
            data = r.json()["data"]
            assert data["turn_id"] == "turn-1"
            assert data["status"] == "queued"
            assert data["dispatch_generation"] == 1
            assert data["correlation_id"].startswith("turn:create:")
            # message + turn + dispatch outbox all committed
            assert session.execute(text("SELECT count(*) FROM agent_messages")).scalar_one() == 1
            assert session.execute(text("SELECT count(*) FROM agent_turns")).scalar_one() == 1
            assert session.execute(text(
                "SELECT count(*) FROM agent_turn_dispatch_outbox WHERE turn_id = 'turn-1'"
            )).scalar_one() == 1
            # status -> 200
            r = c.get("/api/v1/agent-turns/turn-1", headers=headers)
            assert r.status_code == 200
            assert r.json()["data"]["status"] == "queued"
            # messages cursor -> 200
            r = c.get(f"/api/v1/agent-sessions/{session_id}/messages", headers=headers)
            assert r.status_code == 200
            assert len(r.json()["data"]["items"]) == 1
            assert r.json()["data"]["items"][0]["content"] == "Hello"
    finally:
        session.close()


def test_turn_create_idempotent_replay(schema):
    session = _session(schema)
    _seed(session)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            r = c.post("/api/v1/agents/a-1/sessions", json={}, headers=headers)
            session_id = r.json()["data"]["id"]
            body = {"user_message": "Hello", "turn_id": "turn-replay"}
            r1 = c.post(f"/api/v1/agent-sessions/{session_id}/turns", json=body, headers={**headers, "Idempotency-Key": "ag-turn-replay-000001"})
            assert r1.status_code == 202
            r2 = c.post(f"/api/v1/agent-sessions/{session_id}/turns", json=body, headers={**headers, "Idempotency-Key": "ag-turn-replay-000002"})
            assert r2.status_code == 202
            assert r2.json()["data"]["turn_id"] == "turn-replay"
            # replay does NOT duplicate message/turn/dispatch rows
            assert session.execute(text("SELECT count(*) FROM agent_messages")).scalar_one() == 1
            assert session.execute(text("SELECT count(*) FROM agent_turns")).scalar_one() == 1
            assert session.execute(text("SELECT count(*) FROM agent_turn_dispatch_outbox")).scalar_one() == 1
    finally:
        session.close()


def test_turn_cancel_202_and_stream_ticket(schema):
    session = _session(schema)
    _seed(session)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            r = c.post("/api/v1/agents/a-1/sessions", json={}, headers=headers)
            session_id = r.json()["data"]["id"]
            c.post(f"/api/v1/agent-sessions/{session_id}/turns",
                   json={"user_message": "Hello", "turn_id": "turn-c"},
                   headers={**headers, "Idempotency-Key": "ag-turn-cancel-000003"})
            # cancel pre-claim -> 202 cancelled
            r = c.post("/api/v1/agent-turns/turn-c/cancel", json={}, headers={**headers, "Idempotency-Key": "ag-turn-cancel-000004"})
            assert r.status_code == 202
            assert r.json()["data"]["status"] == "cancelled"
            # stream ticket -> 201 single-use secret
            r = c.post("/api/v1/agent-turns/turn-c/stream-ticket", json={}, headers={**headers, "Idempotency-Key": "ag-turn-ticket-000005"})
            assert r.status_code == 201
            data = r.json()["data"]
            assert data["turn_id"] == "turn-c"
            assert data["ticket"]
            assert "stream_ticket_url" in data
            # second ticket revokes the first (only one unused ticket remains)
            r2 = c.post("/api/v1/agent-turns/turn-c/stream-ticket", json={}, headers={**headers, "Idempotency-Key": "ag-turn-ticket-000006"})
            assert r2.status_code == 201
            assert r2.json()["data"]["ticket"] != data["ticket"]
            tickets = session.execute(text(
                "SELECT status FROM agent_stream_tickets WHERE turn_id = 'turn-c'"
            )).scalars().all()
            assert sorted(tickets) == ["revoked", "unused"]
    finally:
        session.close()


def test_turn_api_existence_hiding_and_ownership(schema):
    session = _session(schema)
    _seed(session)
    # a second user with no grant on the agent
    stranger_id = str(uuid.uuid4())
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:id,'st','st@t.com','h','editor',true,:d,now(),now())"
    ), {"id": stranger_id, "d": DEFAULT_DOMAIN})
    session.commit()
    stranger_headers = {"Authorization": f"Bearer {create_access_token({'sub': stranger_id, 'role': 'editor'})}"}
    owner_headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            r = c.post("/api/v1/agents/a-1/sessions", json={}, headers=owner_headers)
            session_id = r.json()["data"]["id"]
            c.post(f"/api/v1/agent-sessions/{session_id}/turns",
                   json={"user_message": "Hello", "turn_id": "turn-x"},
                   headers={**owner_headers, "Idempotency-Key": "ag-turn-owner-0000007"})
            # stranger (no run grant) -> 404 existence-hiding
            assert c.get("/api/v1/agent-turns/turn-x", headers=stranger_headers).status_code == 404
            assert c.post("/api/v1/agent-turns/turn-x/cancel", json={}, headers=stranger_headers).status_code == 404
            assert c.post("/api/v1/agent-turns/turn-x/stream-ticket", json={}, headers=stranger_headers).status_code == 404
            assert c.get(f"/api/v1/agent-sessions/{session_id}", headers=stranger_headers).status_code == 404
            # invalid idempotency key -> 422
            assert c.post(
                f"/api/v1/agent-sessions/{session_id}/turns",
                json={"user_message": "x", "turn_id": "turn-y"},
                headers={**owner_headers, "Idempotency-Key": "short"},
            ).status_code == 422
    finally:
        session.close()


def test_session_close_204(schema):
    session = _session(schema)
    _seed(session)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            r = c.post("/api/v1/agents/a-1/sessions", json={}, headers=headers)
            session_id = r.json()["data"]["id"]
            r = c.delete(f"/api/v1/agent-sessions/{session_id}", headers=headers)
            assert r.status_code == 204
            # turn creation on a closed session -> 423
            r = c.post(f"/api/v1/agent-sessions/{session_id}/turns",
                       json={"user_message": "Hello"},
                       headers={**headers, "Idempotency-Key": "ag-turn-closed-000008"})
            assert r.status_code == 423
    finally:
        session.close()
