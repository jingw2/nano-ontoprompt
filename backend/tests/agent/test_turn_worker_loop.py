"""P3A-DISPATCH -> P4A-WORKER production loop (integration).

The publisher task drains pending outbox rows and enqueues the pinned worker
task; the worker claims with the single CAS, persists the runtime events,
records the assistant response message, terminalizes the Turn under the live
fence, releases the session pointer and resolves the outbox.  Evidence:
publisher args/state, persisted events + message + terminal + pointer +
outbox-resolution, and the fail-closed fence path.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"

EXPECTED_TRANSCRIPT = [
    "turn_started", "resolve_snapshot", "assemble_context",
    "model_call", "final_response", "turn_succeeded",
]


def test_p4a_worker_loop_red_contract():
    failures = []
    for path, symbols in (
        ("app/tasks/agent_dispatch.py", ("agent_dispatch_publish",)),
        ("app/tasks/agent_turn.py", ("finalize_turn_succeeded", "append_event", "record_assistant_message")),
        ("app/services/runtime/finalize.py", ("finalize_turn_succeeded", "record_assistant_message")),
        ("app/services/runtime/events.py", ("append_event",)),
        ("app/tasks/celery_app.py", ("beat_schedule",)),
    ):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
            continue
        source = p.read_text()
        for symbol in symbols:
            if symbol not in source:
                failures.append(f"{path} missing {symbol}")
    if failures:
        pytest.fail("RED_P4A_WORKER_LOOP: " + "; ".join(failures))


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
    schema = "p4a_worker_loop_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0008_agent_tool_selection").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _session(schema):
    return sessionmaker(bind=create_engine(_scoped_url(schema)))()


def _seed_worker_graph(session, *, turn_id="t-1", session_id="s-1", agent_id="a-1",
                       user_message="库存低于安全线的订单有哪些？"):
    """Full dependency graph for the worker query: user, model identity,
    application-state schema (built-in chat-v1 from 0005), agent + active
    version with a bound published ontology (release citation), session,
    queued turn with request message + outbox."""
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:u,'w','w@t.com','h','editor',true,:d,now(),now())"
    ), {"u": "u-1", "d": DEFAULT_DOMAIN})
    # grounded-citation graph: a bound, published ontology whose release the
    # turn's resolve_snapshot cites (I-10).
    import hashlib as _hl
    import json as _json
    session.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-1','Supply','test','v1','published','u-1',now(),now(),:d,1)"
    ), {"d": DEFAULT_DOMAIN})
    manifest = _json.dumps({
        "manifest_version": "ontology-manifest-v1",
        "compiler_version": "ontology-compiler-v1",
        "policy_compiler_version": "restricted-policy-dsl-v1",
        "aggregate_tool_schema_hash": "0" * 64,
        "ontology": {"id": "o-1", "name": "Supply", "security_domain_id": DEFAULT_DOMAIN,
                     "description": None, "build_mode": "simple_llm"},
        "release": {"version_no": 1, "version": "v1"},
        "entities": [{"id": "e-1", "name": "供应商", "type": "Supplier", "description": None,
                      "property_definitions": []}],
        "relations": [],
        "logic_rules": [], "state_machines": [], "actions": [],
        "tool_descriptors": [{"descriptor_id": "query:o-1", "version": 1, "source_kind": "builtin",
                              "source_id": "query", "input_schema": {"query": {"type": "string"}},
                              "output_schema": {"results": {"type": "array"}},
                              "capability": "read_instances", "timeout_ms": 10_000, "result_limit": 10,
                              "descriptor_hash": "0" * 64}],
    }, sort_keys=True)
    manifest_bytes = manifest.encode()
    session.execute(text(
        "INSERT INTO ontology_releases (id, ontology_id, version_no, version, manifest_bytes, "
        "manifest_projection, schema_hash, created_by) "
        "VALUES ('11111111-1111-4111-8111-111111111111', 'o-1', 1, 'v1', :mb, CAST(:proj AS jsonb), :sh, 'u-1')"
    ), {"mb": manifest_bytes, "proj": manifest, "sh": _hl.sha256(manifest_bytes).digest()})
    session.execute(text(
        "UPDATE ontology_projects SET latest_published_release_id = '11111111-1111-4111-8111-111111111111' WHERE id = 'o-1'"
    ))
    session.execute(text(
        "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at) "
        "VALUES ('m-1','m','llm',NULL,'','openai','[]'::json,'{}'::json,'u-1',now(),now())"
    ))
    session.execute(text(
        "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_at) "
        "VALUES ('mv-1','m-1',1,'openai','{}'::json,:hash,'[]'::json,now())"
    ), {"hash": "0" * 64})
    session.execute(text("UPDATE model_configs SET active_version_id = 'mv-1' WHERE id = 'm-1'"))
    schema_id = session.execute(text(
        "SELECT v.id FROM application_state_schema_versions v "
        "JOIN application_state_schema_registries r ON r.active_version_id = v.id "
        "WHERE r.application_key = 'chat-v1'"
    )).scalar_one()
    session.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES (:id,'private','active','u-1',now(),now())"
    ), {"id": agent_id})
    session.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, memory_settings, application_state_schema_version_id, "
        "config_hash, created_by, created_at) "
        "VALUES ('v-1', :agent, 1, 'A', 'mv-1', 'gpt-4o', 'p', '{}'::json, :svid, :hash, 'u-1', now())"
    ), {"agent": agent_id, "svid": schema_id, "hash": "a" * 64})
    session.execute(text(
        "UPDATE agents SET active_version_id = 'v-1' WHERE id = :agent"
    ), {"agent": agent_id})
    session.execute(text(
        "INSERT INTO agent_ontology_bindings (id, agent_version_id, ontology_id, capabilities, allowlists, selected_tools, created_at) "
        "VALUES ('ab-1', 'v-1', 'o-1', CAST(:caps AS jsonb), CAST(:al AS jsonb), CAST(:st AS jsonb), now())"
    ), {"caps": '["read_schema", "read_instances", "traverse_relations"]', "al": '{}',
        "st": '["query:o-1"]'})
    session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) "
        "VALUES (:sid, :aid, 'u-1', 'active')"
    ), {"sid": session_id, "aid": agent_id})
    session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, dispatch_generation, created_at, updated_at) "
        "VALUES (:tid, :sid, 'queued', 1, now(), now())"
    ), {"tid": turn_id, "sid": session_id})
    session.execute(text(
        "UPDATE agent_sessions SET active_turn_id = :tid WHERE id = :sid"
    ), {"tid": turn_id, "sid": session_id})
    message_id = str(uuid.uuid4())
    session.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES (:id, :sid, :turn, 'user', 1, :content, now())"
    ), {"id": message_id, "sid": session_id, "turn": turn_id, "content": user_message})
    session.execute(text(
        "UPDATE agent_turns SET request_message_id = :mid WHERE id = :tid"
    ), {"mid": message_id, "tid": turn_id})
    session.execute(text(
        "INSERT INTO agent_turn_dispatch_outbox (id, turn_id, dispatch_generation, operation, state, created_at) "
        "VALUES (:id, :turn, 1, 'turn', 'pending', now())"
    ), {"id": str(uuid.uuid4()), "turn": turn_id})
    session.commit()


def test_publisher_task_marks_delivered_and_enqueues_worker(schema, monkeypatch):
    session = _session(schema)
    _seed_worker_graph(session)
    session.close()

    scoped = sessionmaker(bind=create_engine(_scoped_url(schema)))
    monkeypatch.setattr("app.database.SessionLocal", scoped)

    from app.tasks.celery_app import celery_app
    calls = []

    def fake_send_task(name, args=(), **kwargs):
        calls.append((name, tuple(args)))
        return SimpleNamespace(id=f"broker-{args[0][:8]}")

    monkeypatch.setattr(celery_app, "send_task", fake_send_task)

    from app.tasks.agent_dispatch import agent_dispatch_publish
    published = agent_dispatch_publish.run()

    assert len(calls) == 1
    task_name, args = calls[0]
    assert task_name == "agent.turn_execute"
    assert args[0] == "t-1"
    assert args[1] == 1                      # dispatch_generation matches the outbox row
    assert args[2].startswith("publish:")    # fresh worker artifact identity
    assert args[3]                            # fresh claim token
    assert published[0]["state"] == "delivered"
    assert published[0]["broker_message_id"] == "broker-t-1"

    session = _session(schema)
    row = session.execute(text(
        "SELECT state, broker_message_id FROM agent_turn_dispatch_outbox WHERE turn_id = 't-1'"
    )).mappings().one()
    assert row["state"] == "delivered"
    assert row["broker_message_id"] == "broker-t-1"
    session.close()


def test_worker_task_persists_and_finalizes_end_to_end(schema, monkeypatch):
    session = _session(schema)
    _seed_worker_graph(session)

    scoped = sessionmaker(bind=create_engine(_scoped_url(schema)))
    monkeypatch.setattr("app.database.SessionLocal", scoped)

    # deliver the outbox (no broker needed for the direct worker invocation)
    from app.services.runtime.dispatch import publish_pending_dispatch
    publish_pending_dispatch(session)

    from app.tasks.agent_turn import agent_turn_execute
    result = agent_turn_execute.run("t-1", 1, "test-worker", "test-token")
    assert result["status"] == "succeeded"
    assert result["events"] == EXPECTED_TRANSCRIPT

    # persisted runtime events (persisted-before-notify, terminal included)
    events = session.execute(text(
        "SELECT event_type FROM agent_runtime_events WHERE turn_id = 't-1' ORDER BY sequence"
    )).scalars().all()
    assert list(events) == EXPECTED_TRANSCRIPT

    # grounded citations (I-10): the resolve_snapshot event carries the pinned
    # release citation + lineage so the OntologyAccessPanel renders it
    snap = session.execute(text(
        "SELECT payload FROM agent_runtime_events "
        "WHERE turn_id = 't-1' AND event_type = 'resolve_snapshot'"
    )).scalar_one()
    assert snap["release_id"] == "11111111-1111-4111-8111-111111111111"
    assert snap["citations"], "resolve_snapshot must carry grounded citations"
    cite = snap["citations"][0]
    assert cite["type"] == "release" and cite["release_id"] == "11111111-1111-4111-8111-111111111111"
    assert cite["version_no"] == 1
    assert cite["entities"] == 1 and cite["relations"] == 0

    # assistant response message inserted with the final answer
    msg = session.execute(text(
        "SELECT role, content FROM agent_messages WHERE turn_id = 't-1' AND role = 'assistant'"
    )).mappings().one()
    assert msg["role"] == "assistant"
    assert msg["content"].startswith("Answer for 库存低于安全线的订单有哪些？")

    # turn terminal with the response message pinned
    turn = session.execute(text(
        "SELECT status, response_message_id FROM agent_turns WHERE id = 't-1'"
    )).mappings().one()
    assert turn["status"] == "succeeded"
    assert turn["response_message_id"] is not None

    # session active pointer cleared
    assert session.execute(text(
        "SELECT active_turn_id FROM agent_sessions WHERE id = 's-1'"
    )).scalar_one() is None

    # dispatch outbox resolved terminal
    outbox = session.execute(text(
        "SELECT state, resolution FROM agent_turn_dispatch_outbox WHERE turn_id = 't-1'"
    )).mappings().one()
    assert outbox["state"] == "resolved_terminal"
    assert outbox["resolution"] == "terminal"
    session.close()


def test_worker_finalization_fence_lost_rolls_back(schema, monkeypatch):
    session = _session(schema)
    _seed_worker_graph(session)
    session.close()

    scoped = sessionmaker(bind=create_engine(_scoped_url(schema)))
    monkeypatch.setattr("app.database.SessionLocal", scoped)

    from app.services.runtime.dispatch import claim_turn, publish_pending_dispatch
    session = _session(schema)
    try:
        # production order: publisher delivers the outbox, then the worker claims
        publish_pending_dispatch(session)
        claim = claim_turn(session, turn_id="t-1", dispatch_generation=1,
                           worker_artifact_id="w-a", claim_token="token-a")
        assert claim["claim_token"] == "token-a"

        from app.services.runtime.finalize import TurnFinalizeError, finalize_turn_succeeded
        with pytest.raises(TurnFinalizeError) as excinfo:
            finalize_turn_succeeded(
                session, turn_id="t-1", session_id="s-1",
                claim_generation=claim["claim_generation"], claim_token="token-b",
                response_message_id="m-x",
            )
        assert "TURN_FENCE_LOST" in str(excinfo.value)

        # nothing terminalized, pointer intact, outbox untouched
        assert session.execute(text(
            "SELECT status FROM agent_turns WHERE id = 't-1'"
        )).scalar_one() == "running"
        assert session.execute(text(
            "SELECT active_turn_id FROM agent_sessions WHERE id = 's-1'"
        )).scalar_one() == "t-1"
        # the claim consumed the delivered row; the fence loss must NOT
        # resolve it (the sweeper recovers the stuck claim)
        assert session.execute(text(
            "SELECT state FROM agent_turn_dispatch_outbox WHERE turn_id = 't-1'"
        )).scalar_one() == "claimed"
    finally:
        session.close()  # release the transaction so the fixture can drop the schema
