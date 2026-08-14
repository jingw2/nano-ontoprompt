"""P3B-RUNTIME: framework-neutral Agent runtime contract.

`AgentRuntime` start/resume/cancel emit typed, persisted `RuntimeEvent`
values; the fake adapter covers start/resume/cancel/failure deterministically
(contract event transcript).  No LangGraph dependency and no business DB.
"""
import asyncio
from pathlib import Path

import pytest

from app.runtime.fake import FakeAgentRuntime, FakeErrorMapping
from app.runtime.protocol import (
    AgentRuntime,
    ResumeSignal,
    RuntimeExecutionError,
    TurnRuntimeContext,
)


BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_p3b_runtime_red_contract():
    failures = []
    for path in ("app/runtime/protocol.py", "app/runtime/fake.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    proto = BACKEND_DIR / "app" / "runtime" / "protocol.py"
    if proto.exists():
        for symbol in ("AgentRuntime", "RuntimeEvent", "TurnRuntimeContext",
                       "ResumeSignal", "start_turn", "resume_turn", "cancel_turn"):
            if symbol not in proto.read_text():
                failures.append(f"protocol.py missing {symbol}")
    if failures:
        pytest.fail("RED_P3B_RUNTIME: " + "; ".join(failures))


def _context(turn_id="t-1", **overrides):
    base = dict(
        turn_id=turn_id, session_id="s-1", agent_id="a-1", agent_version_id="v-1",
        release_id="r-1", model_config_version_id="m-1", model_name="gpt-4o",
        runtime_artifact_id="art-1", user_message="Hello",
    )
    base.update(overrides)
    return TurnRuntimeContext(**base)


def test_fake_is_an_agent_runtime():
    assert isinstance(FakeAgentRuntime(), AgentRuntime)


def test_start_turn_emits_full_graph_transcript():
    async def run():
        runtime = FakeAgentRuntime()
        events = await runtime.start_turn(_context())
        return events, runtime.transcript

    events, transcript = asyncio.run(run())
    assert [e.event_type for e in events] == [
        "turn_started", "resolve_snapshot", "assemble_context",
        "model_call", "final_response", "turn_succeeded",
    ]
    assert events[0].sequence == 1
    assert events[-1].sequence == len(events)
    assert all(e.turn_id == "t-1" for e in events)
    # observable payloads only: pinned ids and the final message
    assert events[1].payload["release_id"] == "r-1"
    assert events[3].payload["model_name"] == "gpt-4o"
    assert events[4].payload["message"].startswith("Answer for")
    assert transcript == [f"start:{k}" for k in [
        "turn_started", "resolve_snapshot", "assemble_context",
        "model_call", "final_response", "turn_succeeded",
    ]]


def test_resume_turn_emits_signal_specific_transcript():
    async def run():
        runtime = FakeAgentRuntime()
        approval = await runtime.resume_turn("t-1", ResumeSignal(kind="approval", reference_id="ap-1"))
        clarification = await runtime.resume_turn("t-2", ResumeSignal(kind="clarification", reference_id="cl-1"))
        retry = await runtime.resume_turn("t-3", ResumeSignal(kind="retry"))
        return approval, clarification, retry, runtime.transcript

    approval, clarification, retry, transcript = asyncio.run(run())
    assert [e.event_type for e in approval] == [
        "validate_approval", "model_call", "final_response", "turn_succeeded",
    ]
    assert approval[0].payload["reference_id"] == "ap-1"
    assert [e.event_type for e in clarification] == [
        "assemble_context", "model_call", "final_response", "turn_succeeded",
    ]
    assert [e.event_type for e in retry] == ["model_call", "final_response", "turn_succeeded"]


def test_cancel_turn_emits_persisted_cancel_event():
    async def run():
        runtime = FakeAgentRuntime()
        events = await runtime.cancel_turn("t-1", actor="u-1")
        return events, runtime.transcript

    events, transcript = asyncio.run(run())
    assert [e.event_type for e in events] == ["turn_cancelled"]
    assert events[0].payload["actor"] == "u-1"
    assert transcript == ["cancel:turn_cancelled"]


def test_failure_maps_to_turn_failed():
    async def run():
        runtime = FakeAgentRuntime(fail_on="always")
        with pytest.raises(RuntimeExecutionError):
            await runtime.start_turn(_context())
        return runtime

    runtime = asyncio.run(run())
    assert FakeErrorMapping().classify(RuntimeExecutionError("x")) == "turn_failed"


def test_resume_cancel_delegates_to_cancel():
    async def run():
        runtime = FakeAgentRuntime()
        events = await runtime.resume_turn("t-1", ResumeSignal(kind="cancel", payload={"actor": "worker"}))
        return events, runtime.transcript

    events, transcript = asyncio.run(run())
    assert [e.event_type for e in events] == ["turn_cancelled"]
    assert transcript == ["cancel:turn_cancelled"]


def test_unsupported_signal_fails_closed():
    async def run():
        runtime = FakeAgentRuntime()
        with pytest.raises(RuntimeExecutionError):
            await runtime.resume_turn("t-1", ResumeSignal(kind="reconciliation"))
        return runtime

    asyncio.run(run())
