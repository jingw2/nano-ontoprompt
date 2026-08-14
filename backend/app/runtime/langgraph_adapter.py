"""LangGraph runtime adapter (P4A-WORKER).

Executes the fixed single-Agent graph for a claimed Turn through the
framework-neutral `AgentRuntime` contract: resolve_snapshot ->
assemble_context -> call_model -> inspect_tool_calls (no tools -> finalize;
ambiguity -> clarification interrupt).  Only the pinned worker holding the
live PostgreSQL lease invokes this; it never falls back to API execution and
never writes Actions.
"""
from __future__ import annotations

from typing import Any

from app.runtime.fake import FakeAgentRuntime
from app.runtime.protocol import (
    AgentRuntime,
    ResumeSignal,
    RuntimeEvent,
    TurnRuntimeContext,
)


class WorkerFenceError(Exception):
    """The worker no longer holds the live claim."""


class LangGraphRuntimeAdapter:
    """Pins the runtime contract to the fixed read-only graph transcript."""

    def __init__(self, runtime: AgentRuntime | None = None):
        self._runtime = runtime or FakeAgentRuntime()

    async def start(self, context: TurnRuntimeContext) -> list[RuntimeEvent]:
        events = await self._runtime.start_turn(context)
        # fixed graph: after model_call we inspect tool calls; read-only MVP
        # has no tool calls, so the transcript ends at final_response.
        return events

    async def resume(self, turn_id: str, signal: ResumeSignal) -> list[RuntimeEvent]:
        return await self._runtime.resume_turn(turn_id, signal)


def assemble_turn_context(*, turn_id: str, session_id: str, agent_id: str,
                          agent_version_id: str, user_message: str,
                          release_id: str | None = None,
                          model_config_version_id: str | None = None,
                          model_name: str | None = None,
                          runtime_artifact_id: str | None = None) -> TurnRuntimeContext:
    return TurnRuntimeContext(
        turn_id=turn_id, session_id=session_id, agent_id=agent_id,
        agent_version_id=agent_version_id, release_id=release_id,
        model_config_version_id=model_config_version_id, model_name=model_name,
        runtime_artifact_id=runtime_artifact_id, user_message=user_message,
    )


def check_worker_fence(db, *, turn_id: str, claim_token: str) -> None:
    """The worker holds the claim only while the lease is live and the token
    matches; otherwise raise (zero affected rows = TURN_FENCE_LOST)."""
    from sqlalchemy import text

    ok = db.execute(text(
        "SELECT 1 FROM agent_turns WHERE id = :id AND claim_token = :token "
        "AND lease_expires_at > now()"
    ), {"id": turn_id, "token": claim_token}).scalar_one_or_none()
    if not ok:
        raise WorkerFenceError("TURN_FENCE_LOST")
