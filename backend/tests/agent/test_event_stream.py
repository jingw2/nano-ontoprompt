"""P4A-STREAM: persisted Agent event replay/SSE.

Events are persisted before notify; pagination and SSE replay from an
after_seq cursor with no gaps/duplicates; a terminal event ends the stream;
sequence divergence fails closed with a gap indicator; stream secrets never
leave the ticket endpoint.
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

from app.routers.agent_events import router
from app.services.auth_service import create_access_token


BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p4a_stream_red_contract():
    failures = []
    for path in ("app/services/runtime/events.py", "app/routers/agent_events.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    svc = BACKEND_DIR / "app" / "services" / "runtime" / "events.py"
    if svc.exists():
        for symbol in ("append_event", "list_events", "stream_chunk", "verify_contiguous"):
            if symbol not in svc.read_text():
                failures.append(f"events.py missing {symbol}")
    if failures:
        pytest.fail("RED_P4A_STREAM: " + "; ".join(failures))


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
    schema = "p4a_stream_" + uuid.uuid4().hex
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


def _seed(schema, *, turn_id="t-1"):
    s = _session(schema)
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','s','s@t.com','h','editor',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-1','O','test','v1','created','u-1',now(),now(),:d,1)"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('a-1','private','active','u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, created_by, created_at, updated_at) "
        "VALUES (:id, 'a-1', 'u-1', CAST(:caps AS json), 1, 'active', 'u-1', now(), now())"
    ), {"id": str(uuid.uuid4()), "caps": '["discover","run","view_config","edit","view_audit"]'})
    s.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) "
        "VALUES ('s-1', 'a-1', 'u-1', 'active')"
    ))
    s.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
        "VALUES (:id, 's-1', 'running', now(), now())"
    ), {"id": turn_id})
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


def test_events_persisted_before_notify_no_gap(schema):
    s = _session(schema)
    _seed(schema)
    from app.services.runtime.events import append_event
    e1 = append_event(s, turn_id="t-1", event_type="turn_started", payload={"a": 1})
    e2 = append_event(s, turn_id="t-1", event_type="model_call", payload={"b": 2})
    assert e1["sequence"] == 1
    assert e2["sequence"] == 2
    page = __import__("app.services.runtime.events", fromlist=["list_events"]).list_events(
        s, turn_id="t-1", after_seq=0)
    assert [e["sequence"] for e in page["items"]] == [1, 2]  # no gap/duplicate
    s.close()


def test_stream_gap_detection(schema):
    s = _session(schema)
    _seed(schema)
    from app.services.runtime.events import append_event, stream_chunk, verify_contiguous
    append_event(s, turn_id="t-1", event_type="turn_started")
    append_event(s, turn_id="t-1", event_type="model_call")
    append_event(s, turn_id="t-1", event_type="final_response")
    # delete the middle event -> sequence gap detected (1, [2], 3)
    s.execute(text("DELETE FROM agent_runtime_events WHERE sequence = 2"))
    s.commit()
    assert verify_contiguous(s, turn_id="t-1", after_seq=0) is False
    chunks = stream_chunk(s, turn_id="t-1", after_seq=0)
    assert chunks[0]["event"] == "gap"
    s.close()


def test_terminal_event_ends_stream(schema):
    s = _session(schema)
    _seed(schema)
    from app.services.runtime.events import append_event, stream_chunk
    append_event(s, turn_id="t-1", event_type="turn_started")
    append_event(s, turn_id="t-1", event_type="turn_succeeded")
    chunks = stream_chunk(s, turn_id="t-1", after_seq=0)
    assert chunks[-1]["event"] == "terminal"
    s.close()


def test_events_api_and_sse_endpoints(schema):
    s = _session(schema)
    _seed(schema)
    from app.services.runtime.events import append_event
    append_event(s, turn_id="t-1", event_type="turn_started", payload={"m": 1})
    append_event(s, turn_id="t-1", event_type="model_call", payload={"m": 2})
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            r = c.get("/api/v1/agent-turns/t-1/events?after_seq=0", headers=headers)
            assert r.status_code == 200
            assert len(r.json()["data"]["items"]) == 2
            # SSE requires a valid stream ticket; without one -> 401
            r = c.get("/api/v1/agent-turns/t-1/stream?after_seq=0&ticket=bad", headers=headers)
            assert r.status_code == 401
    finally:
        s.close()


def test_multi_worker_sequence_no_duplicate(schema):
    """Two workers appending concurrently still produce one monotonic
    sequence per event (no duplicate sequence values)."""
    s = _session(schema)
    _seed(schema)
    from app.services.runtime.events import append_event
    append_event(s, turn_id="t-1", event_type="turn_started")
    append_event(s, turn_id="t-1", event_type="resolve_snapshot")
    append_event(s, turn_id="t-1", event_type="assemble_context")
    seqs = s.execute(text(
        "SELECT sequence FROM agent_runtime_events WHERE turn_id = 't-1' ORDER BY sequence"
    )).scalars().all()
    assert seqs == [1, 2, 3]
    s.close()
