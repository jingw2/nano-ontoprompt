"""P4A-REALRUNTIME: the real Agent runtime calls the model + governed gateway.

`LangGraphRuntime` executes the fixed graph against a pinned immutable caller
(the unit harness injects a deterministic caller + gateway) and emits the
observable transcript: resolve_snapshot (citations) -> assemble_context ->
model_call -> (tool_executed -> model_call)* -> final_response -> terminal.
The final answer is the model's real content — never the canned
"Answer for ..." — and a model error terminalizes with turn_failed.
"""
import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.runtime.langgraph_runtime import LangGraphRuntime
from app.runtime.protocol import TurnRuntimeContext

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# pinned encryption key so any real-caller resolution in this module can never
# depend on module import order (same pattern as the other agent modules)
TEST_FERNET_KEY = Fernet.generate_key().decode()
os.environ["ENCRYPTION_KEY"] = TEST_FERNET_KEY


@pytest.fixture(autouse=True)
def _pin_encryption_key():
    os.environ["ENCRYPTION_KEY"] = TEST_FERNET_KEY
    yield


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR, env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def pg_session():
    """Schema-isolated PostgreSQL session: the REAL ToolGateway executes
    PostgreSQL-only SQL (`::text` casts) that the SQLite `db` harness cannot
    run, so the end-to-end external-tool test runs on a disposable schema
    migrated to the same head as `test_tool_gateway_external.py`."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p7a_runtime_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0018_agent_memory_short_term").returncode == 0
    s = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _context(**overrides) -> TurnRuntimeContext:
    base = dict(
        turn_id="t-1", session_id="s-1", agent_id="a-1", agent_version_id="v-1",
        release_id="r-1", model_config_version_id="mv-1", model_name="mock-chat",
        runtime_artifact_id="art-1", user_message="库存低于安全线的订单有哪些？",
        extra={
            "user_id": "u-1",
            "citations": [{"type": "release", "release_id": "r-1", "version_no": 1,
                           "entities": 1, "relations": 0}],
            "ontology_tool_selection": [{
                "ontology_id": "o-1",
                "capabilities": ["read_schema", "read_instances", "traverse_relations"],
                "selected_tools": ["query:o-1"],
                "enabled_categories": ["query"],
            }],
        },
    )
    base.update(overrides)
    return TurnRuntimeContext(**base)


def _seed_unit_graph(db, *, system_prompt="You are a supply-chain assistant."):
    db.execute(text(
        "INSERT INTO users (id, username, email, password_hash, role, is_active, security_domain_id, created_at, updated_at) "
        "VALUES ('u-1', 'u', 'u@t.com', 'h', 'editor', true, '00000000-0000-0000-0000-000000000001', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    # model chain: agent_versions.default_model_config_version_id is a NOT NULL
    # FK that PostgreSQL enforces (the SQLite harness ignores it but keeps the rows)
    db.execute(text(
        "INSERT INTO model_configs (id, name, config_type, api_base, api_key_encrypted, provider, models, options, created_by, created_at, updated_at) "
        "VALUES ('mc-1', 'm', 'llm', NULL, '', 'openai', '[]', '{}', 'u-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_at) "
        "VALUES ('mv-1', 'mc-1', 1, 'openai', '{}', :h, '[]', CURRENT_TIMESTAMP)"
    ), {"h": "c" * 64})
    db.execute(text(
        "INSERT INTO application_state_schema_registries (id, application_key, status, created_at, updated_at) "
        "VALUES ('reg-1', 'chat-v1-runtime-test', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO application_state_schema_versions (id, registry_id, version_no, json_schema, canonical_hash, created_at) "
        "VALUES ('as-1', 'reg-1', 1, '{\"type\": \"object\", \"properties\": {}}', :h, CURRENT_TIMESTAMP)"
    ), {"h": "b" * 64})
    db.execute(text(
        "UPDATE application_state_schema_registries SET active_version_id = 'as-1' WHERE id = 'reg-1'"
    ))
    db.execute(text(
        "INSERT INTO agents (id, visibility, status, owner_id, created_at, updated_at) "
        "VALUES ('a-1', 'private', 'active', 'u-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, memory_settings, application_state_schema_version_id, "
        "config_hash, created_by, created_at) "
        "VALUES ('v-1', 'a-1', 1, 'A', 'mv-1', 'mock-chat', :sp, '{}', 'as-1', :h, 'u-1', CURRENT_TIMESTAMP)"
    ), {"sp": system_prompt, "h": "a" * 64})
    db.execute(text(
        "UPDATE agents SET active_version_id = 'v-1' WHERE id = 'a-1'"
    ))
    db.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES ('s-1', 'a-1', 'u-1', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    # agent_messages.turn_id is a NOT-NULL-enforced FK on PostgreSQL
    db.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, dispatch_generation, created_at, updated_at) "
        "VALUES ('t-0', 's-1', 'succeeded', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES (:id, 's-1', 't-0', 'user', 1, '你好', CURRENT_TIMESTAMP)"
    ), {"id": str(uuid.uuid4())})
    db.commit()


class FakeGateway:
    def __init__(self):
        self.calls = []

    def execute(self, request, *, ontology_id=None):
        self.calls.append((request.descriptor_id, dict(request.parameters)))
        return type("Result", (), {
            "descriptor_id": request.descriptor_id, "outcome": "read",
            "correlation_id": "gateway:test",
            "payload": {"items": [{"instance_id": "i-1", "entity_id": "e-1",
                                   "row_data": {"name_cn": "华东供应商", "安全线": "500"}}],
                        "correlation_id": "gateway:test"},
        })()


def _run(runtime, context):
    return asyncio.run(runtime.start_turn(context))


def test_real_runtime_calls_model_and_returns_real_answer(db):
    _seed_unit_graph(db)
    # mirror the real invariant (request_message_id is a mandatory FK set
    # before a Turn is ever queued): this test's own turn needs its own
    # persisted request-message row in agent_messages, same as production.
    # A fresh session_id is used (not s-1) because agent_turns has a unique
    # index on session_id for active statuses, and s-1 already holds t-0.
    db.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES ('s-1b', 'a-1', 'u-1', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, dispatch_generation, created_at, updated_at) "
        "VALUES ('t-1', 's-1b', 'running', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES ('m-t1', 's-1b', 't-1', 'user', 1, '库存低于安全线的订单有哪些？', CURRENT_TIMESTAMP)"
    ))
    db.commit()
    seen_questions = []

    def caller(caller_info, messages, tools):
        last_user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        seen_questions.append(last_user)
        return {"content": f"真实回答：{last_user}", "tool_calls": []}

    runtime = LangGraphRuntime(db, caller=caller)
    events = _run(runtime, _context(session_id="s-1b"))
    assert [e.event_type for e in events] == [
        "turn_started", "resolve_snapshot", "assemble_context",
        "model_call", "final_response", "turn_succeeded",
    ]
    # the model was called with the assembled context + the user message
    assert seen_questions == ["库存低于安全线的订单有哪些？"]
    final = events[-2]
    assert final.payload["message"] == "真实回答：库存低于安全线的订单有哪些？"
    assert "Answer for" not in final.payload["message"]
    # observable payloads only
    assert events[1].payload["citations"][0]["release_id"] == "r-1"
    assert events[3].payload["model_config_version_id"] == "mv-1"
    # no release row in the unit harness -> the summary carries only the id
    assert events[2].payload["ontologies"] == [{"ontology_id": "o-1"}]


def test_real_runtime_tool_path_via_gateway(db):
    _seed_unit_graph(db)
    gateway = FakeGateway()
    rounds = []

    def caller(caller_info, messages, tools):
        round_index = len(rounds)
        rounds.append((round_index, [t["function"]["name"] for t in tools]))
        if round_index == 0:
            return {"content": "", "tool_calls": [{
                "id": "call-1", "name": "query_o_1",
                "arguments_json": '{"query": "安全线"}',
            }]}
        return {"content": "根据查询结果：华东供应商的安全线为 500。", "tool_calls": []}

    runtime = LangGraphRuntime(db, caller=caller, gateway=gateway)
    events = _run(runtime, _context())
    types = [e.event_type for e in events]
    assert types.count("model_call") == 2
    assert "tool_executed" in types
    assert types[-3] == "model_call" and types[-2] == "final_response"
    # only the enabled query tool (plus the always-offered clarification tool)
    # was offered to the model
    assert rounds[0][1] == ["query_o_1", "request_clarification"]
    # the gateway received the governed read descriptor with injected params
    assert gateway.calls[0][0] == "ontology.read_instances"
    params = gateway.calls[0][1]
    assert params["ontology_id"] == "o-1" and params["query"] == "安全线"
    executed = next(e for e in events if e.event_type == "tool_executed")
    assert executed.payload["descriptor_id"] == "query:o-1"
    assert executed.payload["outcome"] == "read"
    assert executed.payload["item_count"] == 1
    final = events[-2]
    assert final.payload["message"] == "根据查询结果：华东供应商的安全线为 500。"
    assert "Answer for" not in final.payload["message"]


def test_real_runtime_disabled_category_tool_not_offered(db):
    _seed_unit_graph(db)
    gateway = FakeGateway()

    def caller(caller_info, messages, tools):
        return {"content": "不需要工具。", "tool_calls": []}

    context = _context()
    # only the action category is enabled -> the query tool is filtered out
    context.extra["ontology_tool_selection"] = [{
        "ontology_id": "o-1",
        "capabilities": ["read_schema", "read_instances"],
        "selected_tools": ["query:o-1"],
        "enabled_categories": ["action"],
    }]
    runtime = LangGraphRuntime(db, caller=caller, gateway=gateway)
    events = _run(runtime, context)
    assert "tool_executed" not in [e.event_type for e in events]
    assert gateway.calls == []


def test_real_runtime_unknown_tool_fails_closed(db):
    _seed_unit_graph(db)

    def caller(caller_info, messages, tools):
        return {"content": "", "tool_calls": [{
            "id": "call-x", "name": "not_a_tool", "arguments_json": "{}",
        }]}

    runtime = LangGraphRuntime(db, caller=caller, gateway=FakeGateway())
    events = _run(runtime, _context())
    assert events[-1].event_type == "turn_failed"
    assert events[-1].payload["error_code"] == "TOOL_UNKNOWN"


def test_real_runtime_model_error_terminalizes_failed(db):
    _seed_unit_graph(db)

    def caller(caller_info, messages, tools):
        raise RuntimeError("provider exploded")

    runtime = LangGraphRuntime(db, caller=caller)
    events = _run(runtime, _context())
    assert events[-1].event_type == "turn_failed"
    assert events[-1].payload["error_code"] == "MODEL_CALL_FAILED"
    # no canned answer anywhere in the transcript
    assert all("Answer for" not in str(e.payload) for e in events)


def test_real_runtime_context_budget_exceeded_fails_closed(db, monkeypatch):
    """Regression (P6B-1 Task 4): ContextBudgetExceeded raised from
    _build_messages_and_tools must surface at the start_turn level as a
    turn_failed event with error_code CONTEXT_BUDGET_EXCEEDED — not fall
    through to the generic RUNTIME_EXECUTION_FAILED except-Exception branch,
    and the model must never be called."""
    _seed_unit_graph(db)

    def caller(caller_info, messages, tools):
        raise AssertionError("the model must never be called when the context budget is exceeded")

    def _boom(self, context, assembled):
        from app.services.runtime.message_budget import ContextBudgetExceeded
        raise ContextBudgetExceeded("CONTEXT_BUDGET_EXCEEDED: required material needs 99999 tokens, only 100 available")

    monkeypatch.setattr(
        "app.runtime.langgraph_runtime.LangGraphRuntime._build_messages_and_tools", _boom)
    runtime = LangGraphRuntime(db, caller=caller)
    events = _run(runtime, _context())
    assert [e.event_type for e in events] == [
        "turn_started", "resolve_snapshot", "assemble_context", "turn_failed",
    ]
    assert events[-1].payload["error_code"] == "CONTEXT_BUDGET_EXCEEDED"


def test_real_runtime_uses_pinned_system_prompt_and_context(db):
    _seed_unit_graph(db, system_prompt="你是供应链助手，必须用中文回答。")
    seen = {}

    def caller(caller_info, messages, tools):
        seen["system"] = messages[0]["content"]
        return {"content": "好的。", "tool_calls": []}

    runtime = LangGraphRuntime(db, caller=caller)
    events = _run(runtime, _context())
    assert seen["system"].startswith("你是供应链助手，必须用中文回答。")
    # the assembled context block rides the system prompt (grounded, bounded)
    assert "_agent_available_tools" in seen["system"]
    assert "application_state" in seen["system"]
    assert events[-2].payload["message"] == "好的。"


def _seed_search_binding(db):
    db.execute(text(
        "INSERT INTO tool_providers (id, name, status, kind, created_by, created_at, updated_at) "
        "VALUES ('tp-1', 'Web Search', 'active', 'search', 'u-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO tool_connections (id, provider_id, status, created_by, created_at, updated_at) "
        "VALUES ('tc-1', 'tp-1', 'active', 'u-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO tool_connection_versions (id, connection_id, version_no, endpoint, scopes, "
        "allowlists, approval_status, health_status, created_by, created_at) "
        "VALUES ('tcv-1', 'tc-1', 1, 'https://search.example.com/v1', '[]', '{}', "
        "'approved', 'unknown', 'u-1', CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO agent_external_tool_bindings (id, agent_version_id, tool_connection_version_id, alias, created_at) "
        "VALUES ('aetb-1', 'v-1', 'tcv-1', 'search', CURRENT_TIMESTAMP)"
    ))
    # the real gateway rechecks the user's run grant before external dispatch
    db.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, "
        "created_by, created_at, updated_at) "
        "VALUES ('grant-1', 'a-1', 'u-1', '[\"run\"]', 1, 'active', 'u-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.commit()


def test_real_runtime_external_search_tool_offered_and_dispatched(pg_session, monkeypatch):
    from app.services.untrusted_artifact import make_artifact

    def _fake_web_search(*, endpoint, api_key, query, result_limit=5, timeout_seconds=10.0):
        return [{"title": "OntoPrompt", "url": "https://docs.example.com",
                 "artifact": make_artifact(source="https://docs.example.com", media_type="text/plain",
                                           raw_content="<b>hi from search</b>")}]

    monkeypatch.setattr("app.services.tools.search.web_search", _fake_web_search)
    _seed_unit_graph(pg_session)
    _seed_search_binding(pg_session)
    rounds = []

    def caller(caller_info, messages, tools):
        round_index = len(rounds)
        rounds.append((round_index, [t["function"]["name"] for t in tools]))
        if round_index == 0:
            return {"content": "", "tool_calls": [{
                "id": "call-1", "name": "external_search",
                "arguments_json": '{"query": "ontoprompt docs"}',
            }]}
        return {"content": "根据搜索结果：hi from search。", "tool_calls": []}

    context = _context(extra={
        "user_id": "u-1",
        "citations": [],
        "ontology_tool_selection": [],
        "external_tool_bindings": [{"tool_connection_version_id": "tcv-1", "alias": "search"}],
    })
    runtime = LangGraphRuntime(pg_session, caller=caller)  # real ToolGateway — no FakeGateway substitution
    events = _run(runtime, context)
    assert "external_search" in rounds[0][1]
    executed = next(e for e in events if e.event_type == "tool_executed")
    assert executed.payload["descriptor_id"] == "external.search"
    assert executed.payload["outcome"] == "untrusted_read"
    final = events[-2]
    assert final.payload["message"] == "根据搜索结果：hi from search。"


def _seed_playwright_binding(db):
    db.execute(text(
        "INSERT INTO tool_providers (id, name, status, kind, created_by, created_at, updated_at) "
        "VALUES ('tp-2', 'Web Render', 'active', 'playwright', 'u-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO tool_connections (id, provider_id, status, created_by, created_at, updated_at) "
        "VALUES ('tc-2', 'tp-2', 'active', 'u-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO tool_connection_versions (id, connection_id, version_no, endpoint, scopes, "
        "allowlists, approval_status, health_status, created_by, created_at) "
        "VALUES ('tcv-2', 'tc-2', 1, NULL, '[]', '{\"domains\": [\"example.com\"]}'::json, "
        "'approved', 'unknown', 'u-1', CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO agent_external_tool_bindings (id, agent_version_id, tool_connection_version_id, alias, created_at) "
        "VALUES ('aetb-2', 'v-1', 'tcv-2', 'browser', CURRENT_TIMESTAMP)"
    ))
    # the real gateway rechecks the user's run grant before external dispatch;
    # each test runs in its own schema so this is never a duplicate of the
    # grant seeded by _seed_search_binding
    db.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, "
        "created_by, created_at, updated_at) "
        "VALUES ('grant-1', 'a-1', 'u-1', '[\"run\"]', 1, 'active', 'u-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.commit()


def test_real_runtime_external_playwright_tool_offered_and_dispatched(pg_session, monkeypatch):
    from app.services.untrusted_artifact import make_artifact

    def _fake_browse_page(*, url, allowed_domains, timeout_seconds=20.0, max_bytes=200_000):
        return {"title": "Docs", "final_url": url,
                "artifact": make_artifact(source=url, media_type="text/plain",
                                          raw_content="<b>rendered docs</b>")}

    monkeypatch.setattr("app.services.tools.playwright.browse_page", _fake_browse_page)
    _seed_unit_graph(pg_session)
    _seed_playwright_binding(pg_session)
    rounds = []

    def caller(caller_info, messages, tools):
        round_index = len(rounds)
        rounds.append((round_index, [t["function"]["name"] for t in tools]))
        if round_index == 0:
            return {"content": "", "tool_calls": [{
                "id": "call-1", "name": "external_browser",
                "arguments_json": '{"url": "https://docs.example.com/"}',
            }]}
        return {"content": "根据网页内容：rendered docs。", "tool_calls": []}

    context = _context(extra={
        "user_id": "u-1",
        "citations": [],
        "ontology_tool_selection": [],
        "external_tool_bindings": [{"tool_connection_version_id": "tcv-2", "alias": "browser"}],
    })
    runtime = LangGraphRuntime(pg_session, caller=caller)  # real ToolGateway — no FakeGateway substitution
    events = _run(runtime, context)
    assert "external_browser" in rounds[0][1]
    executed = next(e for e in events if e.event_type == "tool_executed")
    assert executed.payload["descriptor_id"] == "external.playwright"
    assert executed.payload["outcome"] == "untrusted_read"
    final = events[-2]
    assert final.payload["message"] == "根据网页内容：rendered docs。"


def test_real_runtime_playwright_runs_off_the_event_loop(pg_session, monkeypatch):
    """Finding 1 regression: the sync model loop (and therefore the sync
    Playwright adapter) must run OFF the running asyncio loop — Playwright's
    sync API raises inside a running loop, which made every production
    render fail while the sync-only test suite stayed green."""
    from app.services.untrusted_artifact import make_artifact

    def _fake_browse_page(*, url, allowed_domains, timeout_seconds=20.0, max_bytes=200_000):
        # if this runs on the loop thread, get_running_loop() succeeds and
        # the real sync_playwright() would raise — assert the inverse
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        return {"title": "Docs", "final_url": url,
                "artifact": make_artifact(source=url, media_type="text/plain",
                                          raw_content="rendered")}

    monkeypatch.setattr("app.services.tools.playwright.browse_page", _fake_browse_page)
    _seed_unit_graph(pg_session)
    _seed_playwright_binding(pg_session)
    rounds = []

    def caller(caller_info, messages, tools):
        round_index = len(rounds)
        rounds.append(round_index)
        if round_index == 0:
            return {"content": "", "tool_calls": [{
                "id": "call-1", "name": "external_browser",
                "arguments_json": '{"url": "https://docs.example.com/"}',
            }]}
        return {"content": "", "tool_calls": []}

    context = _context(extra={
        "user_id": "u-1", "citations": [], "ontology_tool_selection": [],
        "external_tool_bindings": [{"tool_connection_version_id": "tcv-2", "alias": "browser"}],
    })
    runtime = LangGraphRuntime(pg_session, caller=caller)
    events = asyncio.run(runtime.start_turn(context))
    executed = next(e for e in events if e.event_type == "tool_executed")
    assert executed.payload["outcome"] == "untrusted_read"


def _seed_skill_binding(db) -> str:
    """Seed an approved signed Skill bound as alias `skill` on the unit graph's
    agent version — the manifest carries HTML in its description/instructions
    so the test can assert safe_markdown sanitization in the system prompt."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app.services.agent.configuration import bind_skill
    from app.services.skills import manifest_canonical_hash
    from app.services.skills.admin import approve_skill_version, create_package, create_skill_version

    key = Ed25519PrivateKey.generate()
    manifest = {
        "name": "supplier-skill",
        "description": "read supplier instances <i>safely</i>",
        "instructions": "Use read_suppliers <b>only</b> for supplier data",
        "tools": [{"alias": "read_suppliers", "descriptor_id": "ontology.read_instances",
                   "description": "read supplier instances",
                   "parameters": {"query": "stale-default"}}],
    }
    signature = key.sign(bytes.fromhex(manifest_canonical_hash(manifest)))
    package = create_package(db, actor_id="u-1", name="supplier-skill")
    version = create_skill_version(db, actor_id="u-1", package_id=package["id"],
                                   manifest=manifest,
                                   signatures=[{"public_key_hex": key.public_key().public_bytes_raw().hex(),
                                                "signature_hex": signature.hex()}])
    approve_skill_version(db, actor_id="u-1", version_id=version["id"])
    bind_skill(db, actor_id="u-1", agent_version_id="v-1",
               skill_version_id=version["id"], alias="skill")
    # the real gateway rechecks the user's run grant before external dispatch;
    # each test runs in its own schema so this is never a duplicate of the
    # grant seeded by the other binding seeders
    db.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, "
        "created_by, created_at, updated_at) "
        "VALUES ('grant-1', 'a-1', 'u-1', '[\"run\"]', 1, 'active', 'u-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.commit()
    return version["id"]


def test_real_runtime_skill_tool_offered_and_dispatched(pg_session, monkeypatch):
    _seed_unit_graph(pg_session)
    version_id = _seed_skill_binding(pg_session)
    captured = {}

    def _fake_ontology_read(db, *, descriptor_id, parameters, correlation_id):
        captured["parameters"] = dict(parameters)
        return ("read", {"items": [{"instance_id": "i-1", "entity_id": "e-1",
                                    "row_data": {"name_cn": "华东供应商"}}],
                         "correlation_id": correlation_id})

    monkeypatch.setattr("app.services.ontology_tools.execute_ontology_read", _fake_ontology_read)
    rounds = []

    def caller(caller_info, messages, tools):
        round_index = len(rounds)
        rounds.append((round_index, [t["function"]["name"] for t in tools], messages[0]["content"]))
        if round_index == 0:
            return {"content": "", "tool_calls": [{
                "id": "call-1", "name": "skill_skill",
                "arguments_json": '{"tool": "read_suppliers", "parameters": {}}',
            }]}
        return {"content": "技能结果：read_suppliers 返回了华东供应商。", "tool_calls": []}

    context = _context(extra={
        "user_id": "u-1",
        "citations": [],
        "ontology_tool_selection": [],
        "skill_bindings": [{"skill_version_id": version_id, "alias": "skill"}],
    })
    runtime = LangGraphRuntime(pg_session, caller=caller)  # real ToolGateway — no FakeGateway substitution
    events = _run(runtime, context)
    # one tool per bound skill, named skill_<alias>, with the skill tool enum
    assert "skill_skill" in rounds[0][1]
    # provenance-cited, sanitized instructions injected into the system prompt
    system = rounds[0][2]
    assert "## Signed Skill instructions" in system
    assert "Use read_suppliers only for supplier data" in system  # safe_markdown stripped the <b> tags
    assert "<b>" not in system
    assert "skill:supplier-skill v? (signed)" in system
    # the gateway's external.skill branch dispatched through the signed-version rechecks
    executed = next(e for e in events if e.event_type == "tool_executed")
    assert executed.payload["descriptor_id"] == "external.skill"
    assert executed.payload["outcome"] == "read"
    assert executed.payload["item_count"] == 1  # the leaf result flows into the event
    assert captured["parameters"].get("query") == "stale-default"  # manifest defaults merged in
    final = events[-2]
    assert final.payload["message"] == "技能结果：read_suppliers 返回了华东供应商。"


def _seed_mcp_binding(db) -> str:
    """Seed an approved, schema-pinned, token-issued external_mcp connection
    bound as alias `mcp1` on the unit graph's agent version."""
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone

    from app.services.agent.configuration import bind_external_tool
    from app.services.encryption_service import encrypt
    from app.services.tool_connections import approve_connection_version, create_connection, create_connection_version, create_provider

    provider = create_provider(db, actor_id="u-1", name="mcp-provider", kind="external_mcp")
    connection = create_connection(db, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(
        db, actor_id="u-1", connection_id=connection["id"], endpoint="https://mcp.example.com/rpc",
        scopes=["ontology:query"], allowlists={"domains": ["mcp.example.com"]})
    approve_connection_version(db, actor_id="u-1", version_id=version["id"])
    bind_external_tool(db, actor_id="u-1", agent_version_id="v-1",
                       tool_connection_version_id=version["id"], alias="mcp1")
    db.execute(text(
        "INSERT INTO mcp_connection_schemas (id, connection_version_id, tool_schema_hash, tools, "
        "quarantined, pinned_by, pinned_at) VALUES (:id, :cv, 'pinned-hash', CAST(:tools AS json), "
        "false, 'u-1', now())"
    ), {"id": str(_uuid.uuid4()), "cv": version["id"],
        "tools": '[{"name": "read_suppliers", "description": "d", "input_schema": {}}]'})
    db.execute(text(
        "INSERT INTO mcp_oauth_tokens (id, connection_version_id, encrypted_access_token, "
        "scope, expires_at, issued_by, rotated_at) VALUES (:id, :cv, :tok, "
        "CAST('[\"ontology:query\"]' AS json), :exp, 'u-1', now())"
    ), {"id": str(_uuid.uuid4()), "cv": version["id"], "tok": encrypt("tok-1"),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1)})
    db.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, "
        "created_by, created_at, updated_at) "
        "VALUES ('grant-mcp', 'a-1', 'u-1', '[\"run\"]', 1, 'active', 'u-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.commit()
    return version["id"]


def test_real_runtime_mcp_tool_offered_and_dispatched(pg_session, monkeypatch):
    _seed_unit_graph(pg_session)
    version_id = _seed_mcp_binding(pg_session)
    monkeypatch.setattr("app.services.tools.mcp_client.list_tools", lambda **kw: [
        {"name": "read_suppliers", "description": "d", "input_schema": {}}])
    monkeypatch.setattr("app.services.tools.mcp_client.tools_schema_hash", lambda tools: "pinned-hash")
    monkeypatch.setattr("app.services.tools.mcp_client.call_tool",
                        lambda **kw: {"content": "supplier A", "is_error": False})
    rounds = []
    offered_tools = []

    def caller(caller_info, messages, tools):
        round_index = len(rounds)
        rounds.append((round_index, [t["function"]["name"] for t in tools]))
        if round_index == 0:
            offered_tools.extend(tools)
            return {"content": "", "tool_calls": [{
                "id": "call-1", "name": "mcp_mcp1",
                "arguments_json": '{"tool": "read_suppliers", "parameters": {"query": "supplier"}}',
            }]}
        return {"content": "MCP 返回了 supplier A。", "tool_calls": []}

    context = _context(extra={
        "user_id": "u-1",
        "citations": [],
        "ontology_tool_selection": [],
        "external_tool_bindings": [{"tool_connection_version_id": version_id, "alias": "mcp1"}],
    })
    runtime = LangGraphRuntime(pg_session, caller=caller)
    events = _run(runtime, context)
    assert "mcp_mcp1" in rounds[0][1]
    mcp_tool = next(t for t in offered_tools if t["function"]["name"] == "mcp_mcp1")
    assert "read_suppliers" in mcp_tool["function"]["description"]
    assert "d" in mcp_tool["function"]["description"]
    executed = next(e for e in events if e.event_type == "tool_executed")
    assert executed.payload["descriptor_id"] == "external.mcp"
    assert executed.payload["outcome"] == "untrusted_read"
    final = events[-2]
    assert final.payload["message"] == "MCP 返回了 supplier A。"


def test_start_turn_returns_interrupt_event_without_raising(pg_session, monkeypatch):
    """A TurnInterrupted raised from inside the model loop becomes the final
    RuntimeEvent; start_turn returns normally (it must never propagate the
    exception — agent_turn_execute has no handler for it)."""
    _seed_unit_graph(pg_session)

    def caller(caller_info, messages, tools):
        raise AssertionError("the model must never be called after an interrupt fires pre-loop")

    def _boom(self, context, events, messages, tools):
        from app.runtime.protocol import TurnInterrupted
        raise TurnInterrupted("request_clarification", {"question": "which one?"})

    monkeypatch.setattr("app.runtime.langgraph_runtime.LangGraphRuntime._run_model_loop", _boom)
    context = _context(extra={"user_id": "u-1", "citations": [], "ontology_tool_selection": []})
    runtime = LangGraphRuntime(pg_session, caller=caller)
    events = _run(runtime, context)
    assert events[-1].event_type == "request_clarification"
    assert events[-1].payload == {"question": "which one?"}
    assert not any(e.event_type in ("turn_succeeded", "turn_failed") for e in events)


def test_build_messages_includes_this_turns_own_request_once(pg_session):
    """Fresh-dispatch case: the Turn's own request message is a real row in
    agent_messages by the time _build_messages_and_tools runs (mirroring
    agent_turn_execute's real sequencing) and must appear exactly once.

    Uses its own session/turn ids (not s-1/t-0 from _seed_unit_graph) so this
    test's history isn't polluted by _seed_unit_graph's own seeded message."""
    _seed_unit_graph(pg_session)
    pg_session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES ('s-2', 'a-1', 'u-1', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    pg_session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, dispatch_generation, created_at, updated_at) "
        "VALUES ('t-2', 's-2', 'running', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    pg_session.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES ('m-1', 's-2', 't-2', 'user', 1, '第一个问题', now())"
    ))
    pg_session.commit()
    context = _context(session_id="s-2", turn_id="t-2",
                       extra={"user_id": "u-1", "citations": [], "ontology_tool_selection": []},
                       user_message="第一个问题")
    runtime = LangGraphRuntime(pg_session, caller=lambda *a: {"content": "ok", "tool_calls": []})
    runtime._prepare(context)
    assembled = runtime._assemble_context(context)
    messages, _ = runtime._build_messages_and_tools(context, assembled)
    user_messages = [m for m in messages if m["role"] == "user"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == "第一个问题"


def test_build_messages_includes_injected_resume_answer(pg_session):
    """Resumed-Turn case: a second role='user' row on the SAME turn_id (as
    answer_clarification inserts) must be visible and must NOT trigger a
    duplicate append of the original request.

    Uses its own session/turn ids (not s-1/t-0 from _seed_unit_graph) so this
    test's history isn't polluted by _seed_unit_graph's own seeded message."""
    _seed_unit_graph(pg_session)
    pg_session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES ('s-3', 'a-1', 'u-1', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    pg_session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, dispatch_generation, created_at, updated_at) "
        "VALUES ('t-3', 's-3', 'awaiting_clarification', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    pg_session.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES ('m-1', 's-3', 't-3', 'user', 1, '原始问题不清楚', now())"
    ))
    pg_session.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES ('m-2', 's-3', 't-3', 'user', 2, '这是澄清答案', now())"
    ))
    pg_session.commit()
    context = _context(session_id="s-3", turn_id="t-3",
                       extra={"user_id": "u-1", "citations": [], "ontology_tool_selection": []},
                       user_message="原始问题不清楚")
    runtime = LangGraphRuntime(pg_session, caller=lambda *a: {"content": "ok", "tool_calls": []})
    runtime._prepare(context)
    assembled = runtime._assemble_context(context)
    messages, _ = runtime._build_messages_and_tools(context, assembled)
    user_messages = [m["content"] for m in messages if m["role"] == "user"]
    assert user_messages == ["原始问题不清楚", "这是澄清答案"]


def test_build_messages_survives_budget_with_many_prior_messages(pg_session):
    """Regression (Fix 1): the history query must return the NEWEST N
    messages by ordinal, not the oldest — the old `ORDER BY ordinal LIMIT
    :lim` (ascending) silently returned the OLDEST N rows, dropping the
    current turn's own request whenever a session already had more than
    message_budget (12) prior rows. Seeds 13 prior messages plus this
    turn's own request as the newest row (ordinal 14) and asserts the LAST
    user-role message returned is the current request — proving it
    survives past the budget."""
    _seed_unit_graph(pg_session)
    pg_session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES ('s-4', 'a-1', 'u-1', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    pg_session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, dispatch_generation, created_at, updated_at) "
        "VALUES ('t-4', 's-4', 'running', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    for i in range(1, 14):
        role = "user" if i % 2 == 1 else "assistant"
        pg_session.execute(text(
            "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
            "VALUES (:id, 's-4', 't-4', :role, :ord, :content, now())"
        ), {"id": str(uuid.uuid4()), "role": role, "ord": i, "content": f"历史消息{i}"})
    pg_session.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES ('m-4-own', 's-4', 't-4', 'user', 14, '这是本轮的真实请求', now())"
    ))
    pg_session.commit()
    context = _context(session_id="s-4", turn_id="t-4",
                       extra={"user_id": "u-1", "citations": [], "ontology_tool_selection": []},
                       user_message="这是本轮的真实请求")
    runtime = LangGraphRuntime(pg_session, caller=lambda *a: {"content": "ok", "tool_calls": []})
    runtime._prepare(context)
    assembled = runtime._assemble_context(context)
    messages, _ = runtime._build_messages_and_tools(context, assembled)
    user_messages = [m["content"] for m in messages if m["role"] == "user"]
    assert user_messages[-1] == "这是本轮的真实请求"


def test_build_messages_survives_budget_with_resumed_injection_pair(pg_session):
    """Regression (Fix 1): a resumed turn's injected outcome message (a
    second role='user' row on the same turn_id, mirroring what
    answer_clarification/_resolve_pending_action inject) must also survive
    the message-budget truncation even when the session already has 14
    total messages — proving both the original request AND the injected
    resolution are visible to the model on resume, not evicted by the
    ascending-order bug."""
    _seed_unit_graph(pg_session)
    pg_session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES ('s-5', 'a-1', 'u-1', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    pg_session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, dispatch_generation, created_at, updated_at) "
        "VALUES ('t-5', 's-5', 'running', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    for i in range(1, 13):
        role = "user" if i % 2 == 1 else "assistant"
        pg_session.execute(text(
            "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
            "VALUES (:id, 's-5', 't-5', :role, :ord, :content, now())"
        ), {"id": str(uuid.uuid4()), "role": role, "ord": i, "content": f"历史消息{i}"})
    pg_session.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES ('m-5-req', 's-5', 't-5', 'user', 13, '原始请求', now())"
    ))
    pg_session.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES ('m-5-inj', 's-5', 't-5', 'user', 14, '注入的解决结果', now())"
    ))
    pg_session.commit()
    context = _context(session_id="s-5", turn_id="t-5",
                       extra={"user_id": "u-1", "citations": [], "ontology_tool_selection": []},
                       user_message="原始请求")
    runtime = LangGraphRuntime(pg_session, caller=lambda *a: {"content": "ok", "tool_calls": []})
    runtime._prepare(context)
    assembled = runtime._assemble_context(context)
    messages, _ = runtime._build_messages_and_tools(context, assembled)
    user_messages = [m["content"] for m in messages if m["role"] == "user"]
    assert user_messages[-2:] == ["原始请求", "注入的解决结果"]


def test_real_runtime_summary_included_when_short_term_enabled(db):
    """Regression (final review Finding 5): the summary read path — a real
    agent_memory_summaries row, gated by memory_settings.short_term_enabled,
    assembled through assemble_bounded_messages — must actually reach the
    model on a real Turn, not just the pure assemble_bounded_messages /
    maybe_regenerate_summary unit paths exercised elsewhere."""
    _seed_unit_graph(db)
    db.execute(text(
        "INSERT INTO agent_memory_summaries "
        "(id, session_id, summary_text, covers_from_ordinal, covers_to_ordinal, "
        "source_message_hash, summary_model_name, summary_token_count, updated_at) "
        "VALUES ('sum-1', 's-1', :text, 1, 1, :hash, 'mock-chat', 20, CURRENT_TIMESTAMP)"
    ), {"text": "Confirmed facts: 用户此前已确认订单42的安全线为500", "hash": "s" * 64})
    db.commit()
    captured = {}

    def caller(caller_info, messages, tools):
        captured["messages"] = messages
        return {"content": "ok", "tool_calls": []}

    runtime = LangGraphRuntime(db, caller=caller)
    events = _run(runtime, _context())
    assert events[-1].event_type == "turn_succeeded"
    system_message = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "用户此前已确认订单42的安全线为500" in system_message


def test_real_runtime_summary_excluded_when_short_term_disabled(db):
    """Regression (final review Finding 5): with short_term_enabled=false on
    the Agent version, an existing agent_memory_summaries row must never
    reach the model."""
    _seed_unit_graph(db)
    db.execute(text(
        "UPDATE agent_versions SET memory_settings = '{\"short_term_enabled\": false}' WHERE id = 'v-1'"
    ))
    db.execute(text(
        "INSERT INTO agent_memory_summaries "
        "(id, session_id, summary_text, covers_from_ordinal, covers_to_ordinal, "
        "source_message_hash, summary_model_name, summary_token_count, updated_at) "
        "VALUES ('sum-2', 's-1', :text, 1, 1, :hash, 'mock-chat', 20, CURRENT_TIMESTAMP)"
    ), {"text": "Confirmed facts: 用户此前已确认订单42的安全线为500", "hash": "t" * 64})
    db.commit()
    captured = {}

    def caller(caller_info, messages, tools):
        captured["messages"] = messages
        return {"content": "ok", "tool_calls": []}

    runtime = LangGraphRuntime(db, caller=caller)
    events = _run(runtime, _context())
    assert events[-1].event_type == "turn_succeeded"
    system_message = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "用户此前已确认订单42的安全线为500" not in system_message
