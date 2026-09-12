"""`chat_completion(..., on_delta=...)` streams an OpenAI-compatible
response so the caller can show the answer as it's generated, instead of
only once the full response returns (see `app.services.runtime.
answer_stream`, the Agent-chat live-preview feature this backs)."""
from types import SimpleNamespace

import pytest

from app.services import llm_service


class _FunctionDelta:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _ToolCallDelta:
    def __init__(self, index, id=None, function=None):
        self.index = index
        self.id = id
        self.function = function


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Chunk:
    def __init__(self, delta):
        self.choices = [SimpleNamespace(delta=delta)]


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks
        self.received_kwargs = None

    def create(self, **kwargs):
        self.received_kwargs = kwargs
        return iter(self._chunks)


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeOpenAI:
    """Stand-in for `openai.OpenAI(...)` — captures constructor kwargs and
    hands back a fixed chunk sequence for `.chat.completions.create(...)`."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.chat = _FakeChat(_FakeCompletions(chunks))


@pytest.fixture
def fake_openai(monkeypatch):
    """`llm_service.chat_completion` does a local `import openai` and calls
    `openai.OpenAI(...)` — patching the real module's attribute intercepts
    that call without needing any change to production code."""
    def install(chunks):
        instance = _FakeOpenAI(chunks)
        import openai
        monkeypatch.setattr(openai, "OpenAI", lambda **kw: instance)
        return instance
    return install


def test_streams_content_deltas_live(fake_openai):
    chunks = [
        _Chunk(_Delta(content="你")),
        _Chunk(_Delta(content="好")),
        _Chunk(_Delta(content="！")),
    ]
    fake_openai(chunks)
    seen = []
    result = llm_service.chat_completion(
        "openai", "sk-test", None, "gpt-4o", [{"role": "user", "content": "hi"}],
        on_delta=seen.append,
    )
    assert seen == ["你", "你好", "你好！"]
    assert result == {"content": "你好！", "tool_calls": []}


def test_streams_and_reconstructs_tool_call_argument_fragments(fake_openai):
    chunks = [
        _Chunk(_Delta(tool_calls=[_ToolCallDelta(0, id="call-1", function=_FunctionDelta(name="lookup"))])),
        _Chunk(_Delta(tool_calls=[_ToolCallDelta(0, function=_FunctionDelta(arguments='{"q":'))])),
        _Chunk(_Delta(tool_calls=[_ToolCallDelta(0, function=_FunctionDelta(arguments='"x"}'))])),
    ]
    fake_openai(chunks)
    seen = []
    result = llm_service.chat_completion(
        "openai", "sk-test", None, "gpt-4o", [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "lookup"}}], on_delta=seen.append,
    )
    # no text content ever arrived — nothing meaningful to show live for a tool call
    assert seen == []
    assert result == {
        "content": "",
        "tool_calls": [{"id": "call-1", "name": "lookup", "arguments_json": '{"q":"x"}'}],
    }


def test_without_on_delta_uses_the_existing_blocking_path(monkeypatch):
    """Backward compatibility: every other caller of `chat_completion`
    (extraction, memory summary) never passes `on_delta` and must keep
    getting the plain, non-streaming response shape."""
    class FakeMessage:
        content = "ok"
        tool_calls = []

    class FakeResp:
        choices = [SimpleNamespace(message=FakeMessage())]

    class FakeCompletions:
        def create(self, **kwargs):
            assert "stream" not in kwargs
            return FakeResp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kw):
            self.chat = FakeChat()

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    result = llm_service.chat_completion(
        "openai", "sk-test", None, "gpt-4o", [{"role": "user", "content": "hi"}],
    )
    assert result == {"content": "ok", "tool_calls": []}
