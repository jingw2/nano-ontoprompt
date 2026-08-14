"""Framework-neutral Agent runtime protocol (P3B-RUNTIME, Section 6.1).

`AgentRuntime` is an internal protocol with asynchronous `start_turn`,
`resume_turn` and `cancel_turn` operations that emit framework-neutral
`RuntimeEvent` values.  Only the pinned Celery turn worker holding the live
PostgreSQL lease invokes these operations for a claimed Turn; FastAPI never
calls runtime cancellation directly.  Events expose only persisted,
observable execution data and never hidden reasoning.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Protocol, runtime_checkable


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TurnRuntimeContext:
    """Pinned, immutable context for one Turn execution (Section 6.1)."""

    turn_id: str
    session_id: str
    agent_id: str
    agent_version_id: str
    release_id: str | None = None
    model_config_version_id: str | None = None
    model_name: str | None = None
    runtime_artifact_id: str | None = None
    user_message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


ResumeKind = Literal["approval", "clarification", "retry", "cancel", "reconciliation"]


@dataclass(frozen=True)
class ResumeSignal:
    """A durable signal that resumes or cancels a pinned Turn."""

    kind: ResumeKind
    reference_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


RuntimeEventType = Literal[
    "turn_started",
    "resolve_snapshot",
    "assemble_context",
    "model_call",
    "tool_proposed",
    "tool_executed",
    "request_clarification",
    "approval_required",
    "validate_approval",
    "message",
    "final_response",
    "turn_interrupted",
    "turn_cancelled",
    "turn_failed",
    "turn_succeeded",
]


@dataclass(frozen=True)
class RuntimeEvent:
    """A persisted, observable execution event (never hidden reasoning)."""

    turn_id: str
    event_type: RuntimeEventType
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=_now)


class RuntimeExecutionError(Exception):
    """A runtime execution could not complete; the caller fails closed."""


@runtime_checkable
class AgentRuntime(Protocol):
    """Internal runtime boundary: start/resume/cancel emit RuntimeEvents."""

    async def start_turn(self, context: TurnRuntimeContext) -> list[RuntimeEvent]: ...

    async def resume_turn(self, turn_id: str, signal: ResumeSignal) -> list[RuntimeEvent]: ...

    async def cancel_turn(self, turn_id: str, actor: str) -> list[RuntimeEvent]: ...


class RuntimeErrorMapping(ABC):
    """Maps framework/runtime errors onto the closed RuntimeEvent vocabulary."""

    @abstractmethod
    def classify(self, error: BaseException) -> RuntimeEventType:
        """Return the event type for a failure (defaults to turn_failed)."""
