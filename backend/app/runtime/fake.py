"""Fake Agent runtime adapter (P3B-RUNTIME).

Deterministic `AgentRuntime` implementation for contract tests: `start_turn`
emits the fixed single-Agent graph transcript (resolve_snapshot ->
assemble_context -> model_call -> final_response), `resume_turn` replays the
resume path for approval/clarification/retry/cancel signals, and `cancel_turn`
emits the persisted cancel event.  A `fail_on` option injects a failure that
maps to `turn_failed`.  No LangGraph dependency and no business DB writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.runtime.protocol import (
    AgentRuntime,
    ResumeSignal,
    RuntimeEvent,
    RuntimeExecutionError,
    TurnRuntimeContext,
)


def _seq(events: list[RuntimeEvent], turn_id: str, event_type: str,
         payload: dict | None = None) -> list[RuntimeEvent]:
    events.append(RuntimeEvent(
        turn_id=turn_id, event_type=event_type,  # type: ignore[arg-type]
        sequence=len(events) + 1, payload=payload or {},
    ))
    return events


START_TRANSCRIPT = (
    "turn_started", "resolve_snapshot", "assemble_context",
    "model_call", "final_response", "turn_succeeded",
)
APPROVAL_RESUME = (
    "validate_approval", "model_call", "final_response", "turn_succeeded",
)
CLARIFICATION_RESUME = (
    "assemble_context", "model_call", "final_response", "turn_succeeded",
)
RETRY_RESUME = ("model_call", "final_response", "turn_succeeded")


@dataclass
class FakeAgentRuntime:
    """Scripted runtime: every call appends to `transcript` for contract
    verification; `fail_on` (event sequence number or a sentinel string)
    raises RuntimeExecutionError before emitting."""

    fail_on: str | None = None
    transcript: list[str] = field(default_factory=list)
    _counter: int = field(default=0, init=False)

    def _check_failure(self, turn_id: str) -> None:
        self._counter += 1
        if self.fail_on is not None and (self.fail_on == str(self._counter) or self.fail_on == "always"):
            raise RuntimeExecutionError(f"FAKE_RUNTIME_FAILURE:{turn_id}")

    async def start_turn(self, context: TurnRuntimeContext) -> list[RuntimeEvent]:
        self._check_failure(context.turn_id)
        events: list[RuntimeEvent] = []
        for kind in START_TRANSCRIPT:
            payload = {"turn_id": context.turn_id}
            if kind == "resolve_snapshot":
                payload["release_id"] = context.release_id
                # grounded citations: the pinned release/lineage actually
                # consulted for the Turn's context — observable only, no
                # hidden reasoning (the OntologyAccessPanel renders these).
                payload["citations"] = context.extra.get("citations", [])
            if kind == "assemble_context":
                payload["ontology_tool_selection"] = context.extra.get("ontology_tool_selection", [])
            if kind == "model_call":
                payload["model_config_version_id"] = context.model_config_version_id
                payload["model_name"] = context.model_name
            if kind == "final_response":
                payload["message"] = f"Answer for {context.user_message or context.turn_id}"
            _seq(events, context.turn_id, kind, payload)
        self.transcript.extend(f"start:{kind}" for kind in START_TRANSCRIPT)
        return events

    async def resume_turn(self, turn_id: str, signal: ResumeSignal) -> list[RuntimeEvent]:
        self._check_failure(turn_id)
        events: list[RuntimeEvent] = []
        if signal.kind == "approval":
            transcript = APPROVAL_RESUME
        elif signal.kind == "clarification":
            transcript = CLARIFICATION_RESUME
        elif signal.kind == "retry":
            transcript = RETRY_RESUME
        elif signal.kind == "cancel":
            return await self.cancel_turn(turn_id, signal.payload.get("actor", "worker"))
        else:
            raise RuntimeExecutionError(f"UNSUPPORTED_RESUME_SIGNAL:{signal.kind}")
        for kind in transcript:
            _seq(events, turn_id, kind, {"reference_id": signal.reference_id})
        self.transcript.extend(f"resume:{kind}" for kind in transcript)
        return events

    async def cancel_turn(self, turn_id: str, actor: str) -> list[RuntimeEvent]:
        self._check_failure(turn_id)
        events: list[RuntimeEvent] = []
        _seq(events, turn_id, "turn_cancelled", {"actor": actor})
        self.transcript.append("cancel:turn_cancelled")
        return events


class FakeErrorMapping:
    """Maps runtime exceptions onto the closed event vocabulary."""

    def classify(self, error: BaseException) -> str:
        if isinstance(error, RuntimeExecutionError):
            return "turn_failed"
        return "turn_failed"
