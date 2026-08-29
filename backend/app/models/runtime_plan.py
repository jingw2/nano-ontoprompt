"""Immutable action-plan proposals (Task 15).

A `RuntimePlan` row is a snapshot-pinned, non-writing proposal: it records
what `RuntimeService.create_action_plan` would do, together with the
provenance (snapshot, release, evidence, rule outcomes, policy decision) and
integrity pins (`before_image_hash`, `version_hash`, `precondition_hashes`,
`plan_hash`) needed to detect drift before anything is ever executed. It is
never mutated after insert — the `before_update`/`before_delete` guards below
mirror `SemanticSnapshot`'s append-only pattern (Task 11/12) so the
immutability invariant holds identically on SQLite (unit tests) and
PostgreSQL (production), independent of any DB-level trigger.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, event
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


class ImmutablePlanError(Exception):
    """Raised when any code path attempts to update or delete a persisted
    `RuntimePlan` row. An action plan is a point-in-time proposal; once
    committed it must be superseded by a new plan, never edited in place."""


class RuntimePlan(Base):
    """One immutable, snapshot-pinned action-plan proposal. Never executed or
    connected to a production connector by anything in this module — that is
    an explicit non-goal of `create_action_plan` (Task 15)."""

    __tablename__ = "runtime_plans"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_runtime_plans_id_uuid"),
        CheckConstraint("length(plan_hash) = 64", name="ck_runtime_plans_plan_hash"),
        CheckConstraint("length(before_image_hash) = 64", name="ck_runtime_plans_before_image_hash"),
        CheckConstraint("length(version_hash) = 64", name="ck_runtime_plans_version_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    semantic_snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("semantic_snapshots.id", ondelete="RESTRICT"), nullable=False, index=True,
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
    action_id: Mapped[str] = mapped_column(
        String, ForeignKey("actions.id", ondelete="RESTRICT"), nullable=False,
    )
    input_facts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence_citations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rule_outcomes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    managed_action_binding_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    binding_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    target_key: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    before_image_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    predicted_diff: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    impact_scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    risk_classification: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_decision: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    precondition_hashes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


def _raise_plan_mutation(*_args, **_kwargs):
    raise ImmutablePlanError("RUNTIME_PLAN_IMMUTABLE")


event.listen(RuntimePlan, "before_update", _raise_plan_mutation)
event.listen(RuntimePlan, "before_delete", _raise_plan_mutation)
