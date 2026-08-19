"""P3A-DISPATCH -> P4A-WORKER production loop (integration).

The publisher task drains pending outbox rows and enqueues the pinned worker
task; the worker claims with the single CAS, persists the runtime events,
records the assistant response message, terminalizes the Turn under the live
fence, releases the session pointer and resolves the outbox.  Evidence:
publisher args/state, persisted events + message + terminal + pointer +
outbox-resolution, and the fail-closed fence path.
"""
import json
import os
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"

# pinned encryption key so the seeded credential round-trips (same pattern as
# the model-version test modules)
TEST_FERNET_KEY = Fernet.generate_key().decode()
os.environ["ENCRYPTION_KEY"] = TEST_FERNET_KEY


@pytest.fixture(autouse=True)
def _pin_encryption_key():
    # Other agent test modules define their own ENCRYPTION_KEY; pin ours for
    # every in-process decrypt so module import order cannot break it.
    os.environ["ENCRYPTION_KEY"] = TEST_FERNET_KEY
    yield


def _encrypt(plaintext: str) -> str:
    return Fernet(TEST_FERNET_KEY.encode()).encrypt(plaintext.encode()).decode()


class MockChatHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible mock chat server: answers differ per question and,
    for a question containing the marker word (default 查询), first emits a
    tool call for the exposed query tool and then a grounded answer."""

    tool_marker = "查询"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = body.get("messages", [])
        tools = body.get("tools", [])
        query_tool = next(
            (t["function"]["name"] for t in tools if "query" in t["function"]["name"]), None,
        )
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), "",
        )
        already_answered_tool = any(m.get("role") == "tool" for m in messages)
        if query_tool and self.tool_marker in last_user and not already_answered_tool:
            content = ""
            tool_calls = [{
                "id": "call-mock-1", "type": "function",
                "function": {"name": query_tool,
                             "arguments": json.dumps({"query": "安全线"}, ensure_ascii=False)},
            }]
        else:
            content = f"真实回答：{last_user}"
            tool_calls = []
        resp = {
            "id": "mock-chat-1", "object": "chat.completion", "created": 0,
            "model": "mock-chat",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content,
                                     "tool_calls": tool_calls}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def mock_chat_server():
    server = HTTPServer(("127.0.0.1", 0), MockChatHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    thread.join(timeout=5)

EXPECTED_TRANSCRIPT = [
    "turn_started", "resolve_snapshot", "assemble_context",
    "model_call", "final_response", "turn_succeeded",
]


def test_p4a_worker_loop_red_contract():
    failures = []
    for path, symbols in (
        ("app/tasks/agent_dispatch.py", ("agent_dispatch_publish",)),
        ("app/tasks/agent_turn.py", ("finalize_turn_succeeded", "finalize_turn_failed", "append_event", "record_assistant_message")),
        ("app/services/runtime/finalize.py", ("finalize_turn_succeeded", "finalize_turn_failed", "record_assistant_message")),
        ("app/services/runtime/events.py", ("append_event",)),
        ("app/runtime/langgraph_runtime.py", ("LangGraphRuntime", "start_turn")),
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
    assert _alembic(schema, "upgrade", "0015_external_mcp").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _session(schema):
    return sessionmaker(bind=create_engine(_scoped_url(schema)))()


def _seed_worker_graph(session, *, turn_id="t-1", session_id="s-1", agent_id="a-1",
                       user_message="库存低于安全线的订单有哪些？",
                       api_base="http://127.0.0.1:8123/v1", model_name="mock-chat",
                       with_data_grant=False, with_instance=False,
                       with_action=False, action_id="act-1"):
    """Full dependency graph for the worker query: user, model identity (an
    immutable version + encrypted credential pointing at the mock chat
    server), application-state schema (built-in chat-v1 from 0005), agent +
    active version with a bound published ontology (release citation),
    session, queued turn with request message + outbox.  `with_data_grant`/
    `with_instance` enable the governed query-tool path.  `with_action`
    enables the governed Action propose/approve/execute path."""
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
    action_entries, action_descriptors = [], []
    if with_action:
        action_entries = [{"id": action_id, "name": "ApproveOrder"}]
        action_descriptors = [{
            "descriptor_id": f"action:{action_id}", "version": 1, "source_kind": "action",
            "source_id": action_id, "input_schema": {"parameters": {"type": "object"}},
            "output_schema": {"result": {"type": "object"}},
            "capability": "execute_instance_action", "timeout_ms": 30_000, "result_limit": 1,
            "descriptor_hash": _hl.sha256(f"action:{action_id}".encode()).hexdigest(),
        }]
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
        "logic_rules": [], "state_machines": [], "actions": action_entries,
        "tool_descriptors": [{"descriptor_id": "query:o-1", "version": 1, "source_kind": "builtin",
                              "source_id": "query", "input_schema": {"query": {"type": "string"}},
                              "output_schema": {"results": {"type": "array"}},
                              "capability": "read_instances", "timeout_ms": 10_000, "result_limit": 10,
                              "descriptor_hash": "0" * 64}] + action_descriptors,
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
    contract = _json.dumps([{"provider_model_revision": model_name}], sort_keys=True)
    encrypted_key = _encrypt("sk-mock-chat")
    session.execute(text(
        "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at) "
        "VALUES ('m-1','m','llm',:base,:key,'compatible',CAST(:models AS json),'{}'::json,'u-1',now(),now())"
    ), {"base": api_base, "key": encrypted_key, "models": _json.dumps([model_name])})
    session.execute(text(
        "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, api_base, options, behavior_hash, model_contract, created_at) "
        "VALUES ('mv-1','m-1',1,'compatible',:base,'{}'::json,:hash,CAST(:contract AS jsonb),now())"
    ), {"hash": "0" * 64, "base": api_base, "contract": contract})
    session.execute(text(
        "INSERT INTO model_credentials (id, model_config_id, secret_encrypted, status, secret_revision, created_at) "
        "VALUES ('cred-1', 'm-1', :secret, 'active', 1, now())"
    ), {"secret": encrypted_key})
    session.execute(text("UPDATE model_configs SET active_version_id = 'mv-1' WHERE id = 'm-1'"))
    if with_instance:
        session.execute(text(
            "INSERT INTO entities (id, ontology_id, name_cn, name_en, properties, confidence, version, created_at, updated_at) "
            "VALUES ('e-1', 'o-1', '供应商', 'Supplier', '{}'::json, 0.9, 'v1', now(), now())"
        ))
        session.execute(text(
            "INSERT INTO entity_instances (id, entity_id, ontology_id, row_identity, row_data, created_at) "
            "VALUES ('i-1', 'e-1', 'o-1', 'row-1', CAST(:data AS jsonb), now())"
        ), {"data": _json.dumps({"name_cn": "华东供应商", "安全线": "500", "status": "active"}, ensure_ascii=False)})
    data_caps, access_caps = [], []
    if with_data_grant:
        data_caps.append("read_instances")
        access_caps.append("run")
    if with_action:
        data_caps.append("execute_instance_action")
        if "run" not in access_caps:
            access_caps.append("run")
    if data_caps:
        session.execute(text(
            "INSERT INTO ontology_data_grants (id, ontology_id, user_id, capabilities, policy_version, status, revision, created_by, created_at, updated_at) "
            "VALUES (:id, 'o-1', 'u-1', CAST(:caps AS jsonb), 'restricted-policy-dsl-v1', 'active', 1, 'u-1', now(), now())"
        ), {"id": str(uuid.uuid4()), "caps": _json.dumps(data_caps)})
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
    if access_caps:
        session.execute(text(
            "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, "
            "created_by, created_at, updated_at) "
            "VALUES (:id, :agent, 'u-1', CAST(:caps AS jsonb), 1, 'active', 'u-1', now(), now())"
        ), {"id": str(uuid.uuid4()), "agent": agent_id, "caps": _json.dumps(access_caps)})
    selected_tools = ["query:o-1"] + ([f"action:{action_id}"] if with_action else [])
    session.execute(text(
        "INSERT INTO agent_ontology_bindings (id, agent_version_id, ontology_id, capabilities, allowlists, selected_tools, created_at) "
        "VALUES ('ab-1', 'v-1', 'o-1', CAST(:caps AS jsonb), CAST(:al AS jsonb), CAST(:st AS jsonb), now())"
    ), {"caps": '["read_schema", "read_instances", "traverse_relations"]', "al": '{}',
        "st": _json.dumps(selected_tools)})
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


def _seed_second_turn(session, *, turn_id, session_id, user_message):
    """Add a fresh session + queued turn + message + outbox on the SAME
    agent/version seeded by `_seed_worker_graph` (no duplicate identity rows)."""
    session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) "
        "VALUES (:sid, 'a-1', 'u-1', 'active')"
    ), {"sid": session_id})
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


def test_worker_task_persists_and_finalizes_end_to_end(schema, monkeypatch, mock_chat_server):
    session = _session(schema)
    _seed_worker_graph(session, api_base=mock_chat_server,
                       user_message="库存低于安全线的订单有哪些？")

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

    # the model_call event pins the immutable model version + resolved model
    model_call = session.execute(text(
        "SELECT payload FROM agent_runtime_events "
        "WHERE turn_id = 't-1' AND event_type = 'model_call'"
    )).scalar_one()
    assert model_call["model_config_version_id"] == "mv-1"
    assert model_call["model_name"] == "mock-chat"

    # assistant response message: the REAL model answer, never the canned
    # "Answer for ..." fake
    msg = session.execute(text(
        "SELECT role, content FROM agent_messages WHERE turn_id = 't-1' AND role = 'assistant'"
    )).mappings().one()
    assert msg["role"] == "assistant"
    assert msg["content"].startswith("真实回答：库存低于安全线的订单有哪些？")
    assert "Answer for" not in msg["content"]

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


def test_worker_answer_differs_per_question(schema, monkeypatch, mock_chat_server):
    """The real runtime calls the model: the answer differs per question and
    is never the canned 'Answer for ...' transcript."""
    session = _session(schema)
    _seed_worker_graph(session, api_base=mock_chat_server, turn_id="t-1",
                       user_message="第一个问题")
    _seed_second_turn(session, turn_id="t-2", session_id="s-2",
                      user_message="第二个问题")
    scoped = sessionmaker(bind=create_engine(_scoped_url(schema)))
    monkeypatch.setattr("app.database.SessionLocal", scoped)
    from app.services.runtime.dispatch import publish_pending_dispatch
    publish_pending_dispatch(session)
    from app.tasks.agent_turn import agent_turn_execute
    agent_turn_execute.run("t-1", 1, "w-1", "tok-1")
    agent_turn_execute.run("t-2", 1, "w-2", "tok-2")
    answers = session.execute(text(
        "SELECT turn_id, content FROM agent_messages WHERE role = 'assistant' ORDER BY ordinal"
    )).mappings().all()
    assert len(answers) == 2
    assert answers[0]["content"] == "真实回答：第一个问题"
    assert answers[1]["content"] == "真实回答：第二个问题"
    assert answers[0]["content"] != answers[1]["content"]
    assert all("Answer for" not in a["content"] for a in answers)
    session.close()


def test_worker_tool_path_emits_tool_executed_and_grounded_answer(schema, monkeypatch, mock_chat_server):
    """A question that triggers the query tool produces a tool_executed event
    (via the governed Gateway) and a final answer grounded on the result."""
    session = _session(schema)
    _seed_worker_graph(session, api_base=mock_chat_server, with_data_grant=True,
                       with_instance=True,
                       user_message="查询库存低于安全线的订单有哪些？")
    scoped = sessionmaker(bind=create_engine(_scoped_url(schema)))
    monkeypatch.setattr("app.database.SessionLocal", scoped)
    from app.services.runtime.dispatch import publish_pending_dispatch
    publish_pending_dispatch(session)
    from app.tasks.agent_turn import agent_turn_execute
    result = agent_turn_execute.run("t-1", 1, "w-tool", "tok-tool")
    try:
        assert result["status"] == "succeeded"
        transcript = session.execute(text(
            "SELECT event_type FROM agent_runtime_events WHERE turn_id = 't-1' ORDER BY sequence"
        )).scalars().all()
        # model_call -> tool_executed -> model_call -> final_response
        assert transcript[3] == "model_call"
        assert "tool_executed" in transcript
        assert transcript[-3] == "model_call"
        executed = session.execute(text(
            "SELECT payload FROM agent_runtime_events "
            "WHERE turn_id = 't-1' AND event_type = 'tool_executed'"
        )).scalar_one()
        assert executed["descriptor_id"] == "query:o-1"
        assert executed["outcome"] == "read"
        assert executed["item_count"] == 1
        # the grounded instance row was returned to the model for the final answer
        msg = session.execute(text(
            "SELECT content FROM agent_messages WHERE turn_id = 't-1' AND role = 'assistant'"
        )).scalar_one()
        assert "Answer for" not in msg
    finally:
        session.close()


def test_worker_model_failure_finalizes_turn_failed(schema, monkeypatch):
    """A model error terminalizes the Turn as failed with a persisted
    turn_failed event — never a canned fallback answer."""
    session = _session(schema)
    # point the model at a closed port so the model call fails
    _seed_worker_graph(session, api_base="http://127.0.0.1:1/v1")
    scoped = sessionmaker(bind=create_engine(_scoped_url(schema)))
    monkeypatch.setattr("app.database.SessionLocal", scoped)
    from app.services.runtime.dispatch import publish_pending_dispatch
    publish_pending_dispatch(session)
    from app.tasks.agent_turn import agent_turn_execute
    result = agent_turn_execute.run("t-1", 1, "w-fail", "tok-fail")
    assert result["status"] == "failed"
    assert result["error_code"] == "MODEL_CALL_FAILED"
    events = session.execute(text(
        "SELECT event_type, payload FROM agent_runtime_events WHERE turn_id = 't-1' ORDER BY sequence"
    )).mappings().all()
    assert events[-1]["event_type"] == "turn_failed"
    assert events[-1]["payload"]["error_code"] == "MODEL_CALL_FAILED"
    # no assistant message on failure (no canned answer)
    assert session.execute(text(
        "SELECT count(*) FROM agent_messages WHERE turn_id = 't-1' AND role = 'assistant'"
    )).scalar_one() == 0
    turn = session.execute(text(
        "SELECT status, error_code FROM agent_turns WHERE id = 't-1'"
    )).mappings().one()
    assert turn["status"] == "failed"
    assert turn["error_code"] == "MODEL_CALL_FAILED"
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


def test_worker_task_handles_interrupt_without_finalizing(schema, monkeypatch):
    """A TurnInterrupted-shaped final event must not crash the task and must
    leave the Turn's status exactly as create_clarification/create_approval
    left it (not forced to succeeded/failed)."""
    session = _session(schema)
    _seed_worker_graph(session, user_message="随便问一句")
    scoped = sessionmaker(bind=create_engine(_scoped_url(schema)))
    monkeypatch.setattr("app.database.SessionLocal", scoped)
    from app.services.runtime.dispatch import publish_pending_dispatch
    publish_pending_dispatch(session)

    from app.runtime.protocol import RuntimeEvent

    async def _fake_start(self, context):
        # simulate create_clarification's own side effect (status flip +
        # commit) happening before the interrupt is raised, exactly as the
        # real runtime will do once Task 2 wires it in.  `self` here is the
        # LangGraphRuntimeAdapter; the db session lives on the wrapped
        # LangGraphRuntime instance (`self._runtime.db`), not on the adapter.
        self._runtime.db.execute(text(
            "UPDATE agent_turns SET status = 'awaiting_clarification' WHERE id = :id"
        ), {"id": context.turn_id})
        self._runtime.db.commit()
        return [
            RuntimeEvent(turn_id=context.turn_id, event_type="turn_started", sequence=1, payload={}),
            RuntimeEvent(turn_id=context.turn_id, event_type="request_clarification", sequence=2,
                        payload={"question": "which order?"}),
        ]

    monkeypatch.setattr("app.runtime.langgraph_adapter.LangGraphRuntimeAdapter.start", _fake_start)
    from app.tasks.agent_turn import agent_turn_execute
    result = agent_turn_execute.run("t-1", 1, "w-interrupt", "tok-interrupt")
    assert result["status"] == "interrupted"
    assert result["events"] == ["turn_started", "request_clarification"]
    turn = session.execute(text(
        "SELECT status, response_message_id FROM agent_turns WHERE id = 't-1'"
    )).mappings().one()
    assert turn["status"] == "awaiting_clarification"
    assert turn["response_message_id"] is None  # never finalized
    assert session.execute(text(
        "SELECT content FROM agent_messages WHERE turn_id = 't-1' AND role = 'assistant'"
    )).scalar_one_or_none() is None  # record_assistant_message never called
    session.close()


class ClarifyThenAnswerHandler(BaseHTTPRequestHandler):
    """First call: always asks for clarification. Second call (once the
    transcript contains a role='user' message after the clarification
    question, i.e. the injected answer): gives a grounded final answer that
    quotes the answer, proving the model actually saw it."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = body.get("messages", [])
        user_messages = [m["content"] for m in messages if m["role"] == "user"]
        if len(user_messages) >= 2:
            content = f"已收到澄清：{user_messages[-1]}"
            tool_calls = []
        else:
            content = ""
            tool_calls = [{
                "id": "call-clarify-1", "type": "function",
                "function": {"name": "request_clarification",
                             "arguments": json.dumps({"question": "你说的是哪个订单？"}, ensure_ascii=False)},
            }]
        resp = {
            "id": "mock-chat-1", "object": "chat.completion", "created": 0, "model": "mock-chat",
            "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def clarify_chat_server():
    server = HTTPServer(("127.0.0.1", 0), ClarifyThenAnswerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    thread.join(timeout=5)


def test_worker_clarification_pauses_then_resumes_with_answer_visible(schema, monkeypatch, clarify_chat_server):
    session = _session(schema)
    _seed_worker_graph(session, api_base=clarify_chat_server, user_message="帮我处理一下那个订单")
    scoped = sessionmaker(bind=create_engine(_scoped_url(schema)))
    monkeypatch.setattr("app.database.SessionLocal", scoped)
    from app.services.runtime.dispatch import publish_pending_dispatch
    publish_pending_dispatch(session)
    from app.tasks.agent_turn import agent_turn_execute

    # first dispatch: model asks for clarification, turn pauses
    result = agent_turn_execute.run("t-1", 1, "w-1", "tok-1")
    assert result["status"] == "interrupted"
    assert result["events"][-1] == "request_clarification"
    turn = session.execute(text(
        "SELECT status FROM agent_turns WHERE id = 't-1'"
    )).mappings().one()
    assert turn["status"] == "awaiting_clarification"
    clarification = session.execute(text(
        "SELECT id, question, status FROM agent_clarification_requests WHERE turn_id = 't-1'"
    )).mappings().one()
    assert clarification["question"] == "你说的是哪个订单？"
    assert clarification["status"] == "pending"

    # human answers for real, through the unmodified service function
    from app.services.runtime.clarification import answer_clarification
    answered = answer_clarification(
        session, clarification_id=clarification["id"], actor_id="u-1",
        base_request_revision=1, answer="订单编号 PO-9527",
    )
    assert answered["status"] == "queued"
    assert answered["dispatch_generation"] == 2

    # resume dispatch: model must see the answer and complete
    result2 = agent_turn_execute.run("t-1", 2, "w-2", "tok-2")
    assert result2["status"] == "succeeded"
    final = session.execute(text(
        "SELECT content FROM agent_messages WHERE turn_id = 't-1' AND role = 'assistant'"
    )).scalar_one()
    assert "PO-9527" in final
    turn2 = session.execute(text(
        "SELECT status FROM agent_turns WHERE id = 't-1'"
    )).mappings().one()
    assert turn2["status"] == "succeeded"
    session.close()


class ProposeActionHandler(BaseHTTPRequestHandler):
    """First call: always proposes the bound action tool. Second call (a
    tool result for the action call is already in the transcript): gives a
    final grounded answer quoting the execution id, proving the model saw
    the real execution outcome, not a canned string.  The runtime only ever
    serializes `result["payload"]` back to the model (never the sibling
    "outcome" field, which lives only in the persisted event), so detection
    is by tool-message presence (mirroring `MockChatHandler`'s
    `already_answered_tool` above), not by scanning payload content for an
    outcome label."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = body.get("messages", [])
        tools = body.get("tools", [])
        action_tool = next((t["function"]["name"] for t in tools if t["function"]["name"].startswith("action")), None)
        already_executed = any(m.get("role") == "tool" for m in messages)
        if action_tool and not already_executed:
            content, tool_calls = "", [{
                "id": "call-action-1", "type": "function",
                "function": {"name": action_tool,
                            "arguments": json.dumps({"parameters": {"approve": True}}, ensure_ascii=False)},
            }]
        else:
            tool_msg = next((m["content"] for m in reversed(messages) if m.get("role") == "tool"), "{}")
            execution_id = json.loads(tool_msg).get("execution_id", "")
            content, tool_calls = f"已执行：{execution_id}", []
        resp = {
            "id": "mock-chat-1", "object": "chat.completion", "created": 0, "model": "mock-chat",
            "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def propose_action_chat_server():
    server = HTTPServer(("127.0.0.1", 0), ProposeActionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    thread.join(timeout=5)


def test_worker_action_proposal_pauses_then_executes_on_approval(schema, monkeypatch, propose_action_chat_server):
    session = _session(schema)
    _seed_worker_graph(session, api_base=propose_action_chat_server, with_action=True,
                       user_message="帮我批准这个订单")
    scoped = sessionmaker(bind=create_engine(_scoped_url(schema)))
    monkeypatch.setattr("app.database.SessionLocal", scoped)
    from app.services.runtime.dispatch import publish_pending_dispatch
    publish_pending_dispatch(session)
    from app.tasks.agent_turn import agent_turn_execute

    result = agent_turn_execute.run("t-1", 1, "w-1", "tok-1")
    assert result["status"] == "interrupted"
    assert result["events"][-1] == "approval_required"
    turn = session.execute(text("SELECT status, claim_token FROM agent_turns WHERE id = 't-1'")).mappings().one()
    assert turn["status"] == "awaiting_approval"
    approval = session.execute(text(
        "SELECT a.id, a.revision, a.preview_hash, a.designated_actor_id, te.idempotency_key "
        "FROM agent_approvals a JOIN agent_tool_executions te ON te.id = a.tool_execution_id "
        "WHERE a.turn_id = 't-1'"
    )).mappings().one()
    assert approval["designated_actor_id"] == "u-1"
    assert approval["idempotency_key"] == "call-action-1"

    from app.services.actions.approval import resolve_approval
    resolved = resolve_approval(
        session, approval_id=approval["id"], actor_id="u-1", base_revision=approval["revision"],
        preview_hash=approval["preview_hash"], decision="approved",
    )
    assert resolved["status"] == "approved"
    assert resolved["dispatch_generation"] == 2

    result2 = agent_turn_execute.run("t-1", 2, "w-2", "tok-2")
    assert result2["status"] == "succeeded"
    execution = session.execute(text(
        "SELECT status, result_hash FROM agent_tool_executions WHERE idempotency_key = 'call-action-1'"
    )).mappings().one()
    assert execution["status"] == "succeeded"
    assert execution["result_hash"] is not None
    final = session.execute(text(
        "SELECT content FROM agent_messages WHERE turn_id = 't-1' AND role = 'assistant'"
    )).scalar_one()
    assert execution["result_hash"] in final or "已执行" in final
    session.close()


def test_worker_action_proposal_reports_rejection(schema, monkeypatch, propose_action_chat_server):
    """Same shape, but the human rejects — the model must be told, not
    silently retried or treated as an error."""
    session = _session(schema)
    _seed_worker_graph(session, api_base=propose_action_chat_server, with_action=True,
                       user_message="帮我批准这个订单")
    scoped = sessionmaker(bind=create_engine(_scoped_url(schema)))
    monkeypatch.setattr("app.database.SessionLocal", scoped)
    from app.services.runtime.dispatch import publish_pending_dispatch
    publish_pending_dispatch(session)
    from app.tasks.agent_turn import agent_turn_execute

    agent_turn_execute.run("t-1", 1, "w-1", "tok-1")
    approval = session.execute(text(
        "SELECT id, revision, preview_hash FROM agent_approvals WHERE turn_id = 't-1'"
    )).mappings().one()
    from app.services.actions.approval import resolve_approval
    resolve_approval(
        session, approval_id=approval["id"], actor_id="u-1", base_revision=approval["revision"],
        preview_hash=approval["preview_hash"], decision="rejected",
    )
    result2 = agent_turn_execute.run("t-1", 2, "w-2", "tok-2")
    assert result2["status"] == "succeeded"
    execution = session.execute(text(
        "SELECT status FROM agent_tool_executions WHERE idempotency_key = 'call-action-1'"
    )).mappings().one()
    assert execution["status"] == "cancelled"
    session.close()
