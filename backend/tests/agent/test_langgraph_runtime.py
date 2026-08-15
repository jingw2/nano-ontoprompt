"""P4A-REALRUNTIME: the real Agent runtime calls the model + governed gateway.

`LangGraphRuntime` executes the fixed graph against a pinned immutable caller
(the unit harness injects a deterministic caller + gateway) and emits the
observable transcript: resolve_snapshot (citations) -> assemble_context ->
model_call -> (tool_executed -> model_call)* -> final_response -> terminal.
The final answer is the model's real content — never the canned
"Answer for ..." — and a model error terminalizes with turn_failed.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.runtime.langgraph_runtime import LangGraphRuntime
from app.runtime.protocol import TurnRuntimeContext


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
        "INSERT INTO application_state_schema_registries (id, application_key, status, created_at, updated_at) "
        "VALUES ('reg-1', 'chat-v1', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    db.execute(text(
        "INSERT INTO application_state_schema_versions (id, registry_id, version_no, json_schema, canonical_hash, created_at) "
        "VALUES ('as-1', 'reg-1', 1, '{\"type\": \"object\", \"properties\": {}}', :h, CURRENT_TIMESTAMP)"
    ), {"h": "b" * 64})
    db.execute(text(
        "UPDATE application_state_schema_registries SET active_version_id = 'as-1' WHERE id = 'reg-1'"
    ))
    db.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES ('s-1', 'a-1', 'u-1', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
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
    seen_questions = []

    def caller(caller_info, messages, tools):
        last_user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        seen_questions.append(last_user)
        return {"content": f"真实回答：{last_user}", "tool_calls": []}

    runtime = LangGraphRuntime(db, caller=caller)
    events = _run(runtime, _context())
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
    # only the enabled query tool was offered to the model
    assert rounds[0][1] == ["query_o_1"]
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
    assert "available_tools" in seen["system"]
    assert "application_state" in seen["system"]
    assert events[-2].payload["message"] == "好的。"
