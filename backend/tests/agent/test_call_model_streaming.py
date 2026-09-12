"""`LangGraphRuntime._call_model` wires an `on_delta` callback into
`chat_completion` that publishes to the live-answer-preview channel
(`app.services.runtime.answer_stream`) — this is what lets the Agent
Application chat show the answer as it's generated instead of only once
the whole Turn finishes."""
from app.runtime.langgraph_runtime import LangGraphRuntime
from app.runtime.protocol import TurnRuntimeContext


def _context(turn_id="turn-1"):
    return TurnRuntimeContext(
        turn_id=turn_id, session_id="session-1", agent_id="agent-1",
        agent_version_id="version-1",
    )


def test_call_model_streams_deltas_to_the_answer_stream_channel(monkeypatch):
    runtime = LangGraphRuntime(db=None, caller=None)
    runtime._version_id = None  # skip the DB-backed options lookup
    runtime._caller_info = {
        "provider": "openai", "api_key": "sk-test", "api_base": None, "model": "deepseek-v4-pro",
    }

    captured_on_delta = {}

    def fake_chat_completion(provider, api_key, api_base, model, messages, *, tools=None,
                             options=None, timeout=300, on_delta=None):
        captured_on_delta["fn"] = on_delta
        on_delta("你")
        on_delta("你好")
        return {"content": "你好", "tool_calls": []}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat_completion)

    published = []
    monkeypatch.setattr(
        "app.services.runtime.answer_stream.publish_delta",
        lambda turn_id, text: published.append((turn_id, text)),
    )

    result = runtime._call_model(_context("turn-1"), [{"role": "user", "content": "hi"}], [])

    assert result == {"content": "你好", "tool_calls": []}
    assert captured_on_delta["fn"] is not None
    assert published == [("turn-1", "你"), ("turn-1", "你好")]


def test_publish_tool_status_narrates_a_tool_calling_round(monkeypatch):
    """A tool-calling round produces no text for `on_delta` to stream — this
    is the only live-preview signal during that round, so the chat UI
    doesn't sit on a bare, unchanging spinner while a tool executes."""
    runtime = LangGraphRuntime(db=None, caller=None)
    runtime._name_to_descriptor = {
        "query_o_1": {"descriptor_id": "query:o-1"},
        "logic_rule_1": {"descriptor_id": "logic:rule-1"},
        "unknown_tool": {"descriptor_id": "external.mcp"},
    }

    published = []
    monkeypatch.setattr(
        "app.services.runtime.answer_stream.publish_delta",
        lambda turn_id, text: published.append((turn_id, text)),
    )

    runtime._publish_tool_status(_context("turn-3"), [{"name": "query_o_1"}])
    assert published == [("turn-3", "正在查询本体数据…")]

    published.clear()
    # multiple tool calls in one round: every distinct label is narrated
    runtime._publish_tool_status(_context("turn-3"), [{"name": "query_o_1"}, {"name": "logic_rule_1"}])
    assert published == [("turn-3", "正在查询本体数据…、正在执行逻辑规则…")]

    published.clear()
    # a call the runtime can't resolve a descriptor for still narrates something
    runtime._publish_tool_status(_context("turn-3"), [{"name": "not_a_registered_tool"}])
    assert published == [("turn-3", "正在调用工具…")]


def test_publish_tool_status_is_fail_open_on_a_redis_outage(monkeypatch):
    runtime = LangGraphRuntime(db=None, caller=None)
    runtime._name_to_descriptor = {"query_o_1": {"descriptor_id": "query:o-1"}}

    def raise_on_publish(turn_id, text):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr("app.services.runtime.answer_stream.publish_delta", raise_on_publish)
    # must not raise — a Redis hiccup (or any status-narration failure) is
    # never allowed to fail the Turn
    runtime._publish_tool_status(_context("turn-4"), [{"name": "query_o_1"}])


def test_call_model_with_a_test_double_caller_never_streams(monkeypatch):
    """Every existing test that injects `caller=` (the unit-test double
    path) must keep working unchanged — streaming is production-only."""
    calls = []
    runtime = LangGraphRuntime(db=None, caller=lambda info, messages, tools: {"content": "ok", "tool_calls": []})

    monkeypatch.setattr(
        "app.services.runtime.answer_stream.publish_delta",
        lambda turn_id, text: calls.append((turn_id, text)),
    )

    result = runtime._call_model(_context("turn-2"), [{"role": "user", "content": "hi"}], [])

    assert result == {"content": "ok", "tool_calls": []}
    assert calls == []
