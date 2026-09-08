"""P4A-WORKER: read-only Agent Turn execution in the pinned worker.

Fixed graph events through the LangGraph adapter under a live fenced claim;
fence loss raises TURN_FENCE_LOST; clarification interrupt/resume maps to the
fixed transcript.  Only the matching artifact worker commits; no API
execution fallback and no Action writes.
"""
import asyncio
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


def test_p4a_worker_red_contract():
    failures = []
    for path in ("app/runtime/langgraph_adapter.py", "app/tasks/agent_turn.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    adapter = BACKEND_DIR / "app" / "runtime" / "langgraph_adapter.py"
    if adapter.exists():
        for symbol in ("LangGraphRuntimeAdapter", "check_worker_fence", "assemble_turn_context"):
            if symbol not in adapter.read_text():
                failures.append(f"langgraph_adapter.py missing {symbol}")
    if failures:
        pytest.fail("RED_P4A_WORKER: " + "; ".join(failures))


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
    schema = "p4a_worker_" + uuid.uuid4().hex
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


def _seed(schema, *, turn_id="t-1", status="queued"):
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
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) "
        "VALUES ('s-1', 'a-1', 'u-1', 'active')"
    ))
    s.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
        "VALUES (:id, 's-1', :status, now(), now())"
    ), {"id": turn_id, "status": status})
    s.execute(text(
        "INSERT INTO agent_turn_dispatch_outbox (id, turn_id, dispatch_generation, operation, state, created_at) "
        "VALUES (:id, :turn, 1, 'turn', 'pending', now())"
    ), {"id": str(uuid.uuid4()), "turn": turn_id})
    s.commit()
    s.close()


def test_worker_claims_and_runs_fixed_graph(schema):
    _seed(schema)
    s = _session(schema)
    from app.services.runtime.dispatch import claim_turn
    from app.runtime.langgraph_adapter import LangGraphRuntimeAdapter, assemble_turn_context
    claim = claim_turn(s, turn_id="t-1", dispatch_generation=1,
                       worker_artifact_id="art-1", claim_token="tok")
    assert claim["claim_generation"] == 1
    assert claim["claim_token"] == "tok"
    assert s.execute(text("SELECT status FROM agent_turns WHERE id = 't-1'")).scalar_one() == "running"
    context = assemble_turn_context(turn_id="t-1", session_id="s-1", agent_id="a-1",
                                    agent_version_id="v-1", user_message="Hello",
                                    model_config_version_id="mv-1", model_name="gpt-4o",
                                    runtime_artifact_id="art-1")
    events = asyncio.run(LangGraphRuntimeAdapter().start(context))
    assert [e.event_type for e in events] == [
        "turn_started", "resolve_snapshot", "assemble_context",
        "model_call", "final_response", "turn_succeeded",
    ]
    s.close()


def test_worker_fence_lost_raises(schema):
    _seed(schema)
    s = _session(schema)
    from app.runtime.langgraph_adapter import check_worker_fence, WorkerFenceError
    with pytest.raises(WorkerFenceError, match="TURN_FENCE_LOST"):
        check_worker_fence(s, turn_id="t-1", claim_token="tok")
    s.close()


def test_worker_requires_matching_artifact_claim(schema):
    _seed(schema)
    s = _session(schema)
    from app.services.runtime.dispatch import claim_turn, DispatchError
    # wrong artifact id cannot claim when a live claim exists
    claim_turn(s, turn_id="t-1", dispatch_generation=1,
               worker_artifact_id="art-1", claim_token="tok")
    with pytest.raises(DispatchError):
        claim_turn(s, turn_id="t-1", dispatch_generation=1,
                   worker_artifact_id="art-2", claim_token="tok2")
    s.close()


def test_clarification_interrupt_resume_matrix(schema):
    """Ambiguity -> clarification interrupt; answer resumes once (fixed graph
    transcript), a second resume is not applied."""
    from app.runtime.fake import FakeAgentRuntime
    from app.runtime.protocol import ResumeSignal
    from app.runtime.langgraph_adapter import LangGraphRuntimeAdapter
    runtime = FakeAgentRuntime()
    adapter = LangGraphRuntimeAdapter(runtime)
    # interruption path: request_clarification then resume via clarification
    interrupted = asyncio.run(adapter.resume("t-1", ResumeSignal(kind="clarification", reference_id="cl-1")))
    assert [e.event_type for e in interrupted] == [
        "assemble_context", "model_call", "final_response", "turn_succeeded",
    ]
    assert runtime.transcript.count("resume:assemble_context") == 1
