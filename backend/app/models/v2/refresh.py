"""Durable source refresh contract — persistence (Task 6).

Authoritative source cursor state (`RefreshSourceState`), run leases with
fencing (`RefreshRun`), durable inbox/outbox/dead-letter identity, and
schedules. Table names deliberately do not carry the legacy `v2_` prefix
(matching the newer `agent_*`/`skill_*` convention) so
`tests/v2/models/test_v2_schema.py`'s exact-set check of `v2_`-prefixed
tables is unaffected by this migration.

Governed by `app.services.v2.incremental.contract`, which is the only
writer of lease/fencing/cancellation state; do not mutate these rows
directly outside that module (except test fixtures).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RefreshSourceState(Base):
    """Authoritative mutable singleton for exactly one (source_id, resource)."""

    __tablename__ = "refresh_source_states"
    __table_args__ = (
        UniqueConstraint("source_id", "resource", name="uq_refresh_source_states_source_resource"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    resource: Mapped[str] = mapped_column(String(200), nullable=False)

    cursor_contract: Mapped[str] = mapped_column(String(30), nullable=False)
    cursor_json: Mapped[dict | None] = mapped_column("cursor", JSON, nullable=True)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    configuration: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_successful_run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("refresh_runs.id", ondelete="SET NULL"), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    @property
    def cursor(self):
        from app.schemas.refresh import SourceCursor

        data = self.cursor_json
        if data is None:
            return None
        observed_at = data.get("observed_at")
        return SourceCursor(
            source_id=self.source_id,
            resource=self.resource,
            contract=self.cursor_contract,
            watermark=data.get("watermark"),
            primary_key=data.get("primary_key"),
            opaque_value=data.get("opaque_value"),
            observed_at=datetime.fromisoformat(observed_at) if observed_at else None,
        )


class RefreshRun(Base):
    """One refresh attempt: frozen source revision, lease/fencing, dispatch
    state, cursor before/after, lineage pointer, cancellation state."""

    __tablename__ = "refresh_runs"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "resource", "config_version", "idempotency_key",
            name="uq_refresh_runs_idempotency_scope",
        ),
        CheckConstraint(
            "status IN ('queued','running','cancel_requested','cancelled','succeeded','failed','dead_lettered')",
            name="ck_refresh_runs_status",
        ),
        CheckConstraint(
            "dispatch_state IN ('pending','dispatched','backpressured','publish_failed')",
            name="ck_refresh_runs_dispatch_state",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    resource: Mapped[str] = mapped_column(String(200), nullable=False)

    policy: Mapped[str] = mapped_column(String(20), nullable=False)
    trigger: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Frozen source revision at claim time — compared against the
    # authoritative RefreshSourceState by record_refresh_outcome before any
    # lease/fencing check.
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    cursor_contract: Mapped[str] = mapped_column(String(30), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    dispatch_state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    dispatch_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Durable destination queue attribution for operability metrics. Nullable
    # keeps pre-Task-7 runs readable; new dispatch paths record the exact
    # queue selected by the Celery topology.
    dispatch_queue: Mapped[str | None] = mapped_column(String(120), nullable=True)

    cursor_before_json: Mapped[dict | None] = mapped_column("cursor_before", JSON, nullable=True)
    cursor_after_json: Mapped[dict | None] = mapped_column("cursor_after", JSON, nullable=True)

    # Output/pipeline lineage pointer. Deliberately not a hard foreign key:
    # record_refresh_outcome persists whatever pipeline_run_id/dataset
    # version ids a governed connector/orchestrator hands it (a later task's
    # concern), and a rolled-back outcome must never have created — or been
    # blocked by the absence of — those rows.
    pipeline_run_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    source_provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lag_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duplicate_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    late_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)

    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False)

    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_fencing_token: Mapped[int | None] = mapped_column(Integer, nullable=True)

    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    # Transient, non-persisted flag set only by request_refresh_cancellation.
    # A plain (non-Mapped) class attribute — SQLAlchemy's declarative system
    # only maps attributes annotated with `Mapped[...]`, so this is never a
    # column; it's a Python-level default that also applies to instances
    # loaded by the ORM (which bypass __init__).
    already_terminal: bool = False

    @property
    def cursor_before(self):
        return self._cursor_from_json(self.cursor_before_json)

    @property
    def cursor_after(self):
        return self._cursor_from_json(self.cursor_after_json)

    def _cursor_from_json(self, data):
        from app.schemas.refresh import SourceCursor

        if data is None:
            return None
        observed_at = data.get("observed_at")
        return SourceCursor(
            source_id=self.source_id,
            resource=self.resource,
            contract=self.cursor_contract,
            watermark=data.get("watermark"),
            primary_key=data.get("primary_key"),
            opaque_value=data.get("opaque_value"),
            observed_at=datetime.fromisoformat(observed_at) if observed_at else None,
        )


class RefreshRunTransition(Base):
    """Append-only run status transition history."""

    __tablename__ = "refresh_run_transitions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("refresh_runs.id", ondelete="CASCADE"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RefreshInboxEvent(Base):
    """Durable inbox identity for managed webhook/outbox ingestion — one row
    per (source_id, resource, event_id), enforced by a unique constraint."""

    __tablename__ = "refresh_inbox_events"
    __table_args__ = (
        UniqueConstraint("source_id", "resource", "event_id", name="uq_refresh_inbox_events_identity"),
        CheckConstraint(
            "state IN ('received','duplicate','processed','dead_lettered')",
            name="ck_refresh_inbox_events_state",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    resource: Mapped[str] = mapped_column(String(200), nullable=False)
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RefreshOutboxEvent(Base):
    """Durable outbound delivery record for downstream consumers."""

    __tablename__ = "refresh_outbox_events"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending','published','failed')",
            name="ck_refresh_outbox_events_state",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    resource: Mapped[str] = mapped_column(String(200), nullable=False)
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RefreshDeadLetter(Base):
    """Durable dead-letter record for an inbox event or run that exhausted
    retries; replay is tracked via `replay_status`."""

    __tablename__ = "refresh_dead_letters"
    __table_args__ = (
        CheckConstraint(
            "replay_status IN ('pending','replayed','discarded')",
            name="ck_refresh_dead_letters_replay_status",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    resource: Mapped[str] = mapped_column(String(200), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String, ForeignKey("refresh_runs.id", ondelete="SET NULL"), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replay_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RefreshSchedule(Base):
    """Cron/business-calendar schedule for a source or pipeline target."""

    __tablename__ = "refresh_schedules"
    __table_args__ = (
        UniqueConstraint("target_type", "target_id", name="uq_refresh_schedules_target"),
        CheckConstraint("target_type IN ('source','pipeline')", name="ck_refresh_schedules_target_type"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(200), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    business_calendar_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    excluded_dates: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sla_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    backfill_window_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Admission control (Task 7): dispatch_due_schedules refuses to publish a
    # due schedule's run once its source already has this many queued/running
    # runs, keeping it durably queued with dispatch_state="backpressured"
    # instead of piling up unbounded work. Added by 0023_refresh_schedule_admission.
    max_pending_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(default=True)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_dispatched_run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("refresh_runs.id", ondelete="SET NULL"), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    @property
    def business_calendar(self) -> list | None:
        """Alias for `excluded_dates` — the list of ISO dates this schedule
        skips (Task 7's `dispatch_due_schedules` business-calendar check)."""
        return self.excluded_dates
