"""P6B-3: memory inspection/correction/deletion REST API (Task 6).
Spec: docs/superpowers/plans/2026-08-09-agent-ontology-implementation.md,
Section 12.1. API-level coverage over the router wiring the already-merged
`app.services.memory.inspection` service functions (Tasks 2-5) to HTTP."""
import json
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

from app.routers.agent_memories import router
from app.services.auth_service import create_access_token

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args], cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p6b3_api_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0020_agent_memory_recall_index").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _session(schema):
    return sessionmaker(bind=create_engine(_scoped_url(schema)))()


def _seed(schema, *, user_id="u-1", agent_id="ag-1"):
    s = _session(schema)
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,"
        "created_at,updated_at) VALUES (:u,'a','a@t.com','h','editor',true,:d,now(),now())"
    ), {"u": user_id, "d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES (:id,'private','active',:u,now(),now())"
    ), {"id": agent_id, "u": user_id})
    s.commit()
    s.close()


def _client(session):
    from app.deps import get_db

    def override_get_db():
        yield session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


def _insert_memory(session, *, memory_id, user_id="u-1", agent_id="ag-1",
                   subject_key="self", predicate="user.name",
                   display_text="User's name is Alex", confidence=0.9,
                   consent_basis="explicit_statement", status="active"):
    session.execute(text(
        "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
        "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
        "consent_basis, source_spans, status, created_at, updated_at) "
        "VALUES (:id, :d, :a, :u, 'semantic', :sk, :pred, CAST(:val AS jsonb), :hash, :disp, :conf, "
        "'low', :consent_basis, CAST('[0]' AS jsonb), :status, now(), now())"
    ), {"id": memory_id, "d": DEFAULT_DOMAIN, "a": agent_id, "u": user_id, "sk": subject_key,
        "pred": predicate, "val": json.dumps(display_text), "hash": f"hash-{memory_id}",
        "disp": display_text, "conf": confidence, "consent_basis": consent_basis,
        "status": status})
    session.execute(text(
        "INSERT INTO agent_memory_revisions (id, memory_id, revision_no, canonical_value, "
        "display_text, confidence, consent_basis, source_spans, created_by, created_at) "
        "VALUES (:id, :mid, 1, CAST(:val AS jsonb), :disp, :conf, :consent_basis, "
        "CAST('[0]' AS jsonb), :u, now())"
    ), {"id": f"rev-{memory_id}", "mid": memory_id, "val": json.dumps(display_text),
        "disp": display_text, "conf": confidence, "consent_basis": consent_basis, "u": user_id})
    session.commit()


def _insert_user(session, user_id):
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,"
        "created_at,updated_at) VALUES (:u,:u,:u,'h','editor',true,:d,now(),now())"
    ), {"u": user_id, "d": DEFAULT_DOMAIN})
    session.commit()


def _insert_conflict(session, *, conflict_id, winner_id, loser_id, user_id="u-1",
                     agent_id="ag-1"):
    session.execute(text(
        "INSERT INTO agent_memory_conflicts (id, security_domain_id, agent_id, user_id, "
        "subject_key, predicate, memory_id_a, memory_id_b, status, created_at) "
        "VALUES (:id, :d, :a, :u, 'self', 'user.name', :winner, :loser, 'open', now())"
    ), {"id": conflict_id, "d": DEFAULT_DOMAIN, "a": agent_id, "u": user_id,
        "winner": winner_id, "loser": loser_id})
    session.commit()


def test_list_memories_endpoint_returns_only_current_users_memories(schema):
    _seed(schema)
    s = _session(schema)
    _insert_user(s, "u-2")
    _insert_memory(s, memory_id="mem-1", user_id="u-1")
    _insert_memory(s, memory_id="mem-2", user_id="u-2", subject_key="self",
                   predicate="user.preference")
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            r = c.get("/api/v1/agents/ag-1/memories", headers=headers)
            assert r.status_code == 200
            items = r.json()["data"]["items"]
            assert [m["id"] for m in items] == ["mem-1"]
    finally:
        s.close()


def test_get_memory_endpoint_404_for_other_users_memory(schema):
    _seed(schema)
    s = _session(schema)
    _insert_user(s, "u-2")
    _insert_memory(s, memory_id="mem-2", user_id="u-2")
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            r = c.get("/api/v1/agents/ag-1/memories/mem-2", headers=headers)
            assert r.status_code == 404
    finally:
        s.close()


def test_confirm_memory_endpoint_without_consent_returns_error(schema):
    _seed(schema)
    s = _session(schema)
    _insert_memory(s, memory_id="mem-1", status="pending_confirmation",
                   consent_basis="explicit_confirmation")
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            r = c.post("/api/v1/agents/ag-1/memories/mem-1/confirm",
                       json={"consent": False}, headers=headers)
            assert 400 <= r.status_code < 500
            assert r.json()["detail"] == "MEMORY_CONSENT_REQUIRED"
    finally:
        s.close()


def test_correct_memory_endpoint_updates_display_text(schema):
    _seed(schema)
    s = _session(schema)
    _insert_memory(s, memory_id="mem-1", display_text="User's name is Alex")
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            r = c.post("/api/v1/agents/ag-1/memories/mem-1/correct",
                       json={"display_text": "User's name is Alexander", "confidence": 0.8},
                       headers=headers)
            assert r.status_code == 200
            assert r.json()["data"]["display_text"] == "User's name is Alexander"
    finally:
        s.close()


def test_delete_memory_endpoint_tombstones(schema):
    _seed(schema)
    s = _session(schema)
    _insert_memory(s, memory_id="mem-1", status="active")
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            r = c.post("/api/v1/agents/ag-1/memories/mem-1/delete", headers=headers)
            assert r.status_code in (200, 204)
            # get_memory (Task 2, already merged/tested) intentionally does not
            # filter by status -- an inspection detail view must still be able
            # to show a tombstoned memory -- so the follow-up GET is 200 with
            # status "deleted", not 404.
            r = c.get("/api/v1/agents/ag-1/memories/mem-1", headers=headers)
            assert r.status_code == 200
            assert r.json()["data"]["status"] == "deleted"
    finally:
        s.close()


def test_resolve_conflict_endpoint_picks_winner(schema):
    _seed(schema)
    s = _session(schema)
    _insert_memory(s, memory_id="mem-a", status="conflicted", display_text="Alex")
    _insert_memory(s, memory_id="mem-b", status="conflicted", display_text="Alexandra",
                   subject_key="self")
    _insert_conflict(s, conflict_id="conf-1", winner_id="mem-a", loser_id="mem-b")
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            r = c.post("/api/v1/agents/ag-1/memories/conflicts/conf-1/resolve",
                       json={"winning_memory_id": "mem-a"}, headers=headers)
            assert r.status_code == 200
            assert r.json()["data"]["status"] == "active"
            winner = s.execute(text(
                "SELECT status FROM agent_memories WHERE id = 'mem-a'"
            )).mappings().one()
            assert winner["status"] == "active"
    finally:
        s.close()
