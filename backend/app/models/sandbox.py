"""Immutable Sandbox simulation results (Task 22).

A `SandboxSimulation` row records what `simulate_action`
(`app.services.runtime.sandbox`) computed for one immutable `RuntimePlan`
(Task 15) at simulation time: the expected row impact, a field-level
before/after diff, the rule/policy outcomes and precondition hashes it was
computed from, and when the simulation itself expires. It is never mutated
after insert — the `before_update`/`before_delete` guards below mirror
`RuntimePlan`'s append-only pattern (Task 15) so the immutability invariant
holds identically on SQLite (unit tests) and PostgreSQL (production),
independent of any DB-level trigger.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

UUID_CHECK = (
    "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$'"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class SandboxImmutableError(Exception):
    """Raised when any code path attempts to update or delete a persisted
    `SandboxSimulation` row. A simulation is a point-in-time result; once
    committed it must be superseded by a new simulation, never edited in
    place."""


class SandboxSimulation(Base):
    """One immutable simulation result computed against a pinned
    `RuntimePlan`'s snapshot and (optional) managed action binding. Never
    executed or connected to a production connector by anything in this
    module — Sandbox v1 builds no connector integration, an explicit
    non-goal shared with Task 21's `freeze_action_target`."""

    __tablename__ = "sandbox_simulations"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_sandbox_simulations_id_uuid"),
        CheckConstraint(
            "(managed_action_binding_id IS NULL) = (binding_version IS NULL)",
            name="ck_sandbox_simulations_binding_pair",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    action_plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runtime_plans.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    semantic_snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("semantic_snapshots.id", ondelete="RESTRICT"), nullable=False,
    )
    ontology_release_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ontology_releases.id", ondelete="RESTRICT"), nullable=False,
    )
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    managed_action_binding_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("managed_action_bindings.managed_action_binding_id", ondelete="RESTRICT"),
        nullable=True,
    )
    binding_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    expected_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    before_after_diff: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    impact_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rule_outcome: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    policy_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    precondition_hashes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="simulated")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


def _raise_simulation_mutation(*_args, **_kwargs):
    raise SandboxImmutableError("SANDBOX_SIMULATION_IMMUTABLE")


event.listen(SandboxSimulation, "before_update", _raise_simulation_mutation)
event.listen(SandboxSimulation, "before_delete", _raise_simulation_mutation)
