"""Authenticated, durable refresh operations.

This module is intentionally an adapter around the refresh services from
Tasks 6--9.  It resolves source-owned state from the database, commits a
durable run/schedule/cancellation transition, and leaves connector execution
to the run-ID-only workers.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2.connection import Connection
from app.models.v2.pipeline import PipelineRunInput
from app.models.v2.refresh import (
    RefreshDeadLetter,
    RefreshRun,
    RefreshRunTransition,
    RefreshSchedule,
    RefreshSourceState,
)
from app.schemas.refresh import RefreshError, RefreshPolicy
from app.services.v2.incremental.contract import (
    _lock_source_state,
    _lock_or_create_source_state,
    request_refresh_cancellation,
)
from app.services.v2.incremental.event_ingest import EventIngestService
from app.services.v2.incremental.operability import (
    QueueObservation,
    RefreshOperability,
    collect_refresh_operability,
)
from app.services.v2.scheduler.schedule_service import (
    DEFAULT_RESOURCE,
    ScheduleRequest,
    upsert_refresh_schedule,
)


class RefreshRunView(BaseModel):
    """Non-secret, serializable view of a durable refresh run."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    run_id: str
    source_id: str
    resource: str
    policy: str
    trigger: str | None = None
    status: str
    dispatch_state: str | None = None
    dispatch_queue: str | None = None
    config_version: int
    cursor_contract: str
    cursor_before: dict[str, Any] | None = None
    cursor_after: dict[str, Any] | None = None
    input_dataset_version_ids: list[str] = Field(default_factory=list)
    pipeline_run_id: str | None = None
    lag_seconds: int | None = None
    duplicate_count: int = 0
    late_count: int = 0
    retry_count: int = 0
    retry_reason: str | None = None
    dead_letter_id: str | None = None
    replay_status: str | None = None
    cancel_requested_at: datetime | None = None
    cancel_requested_by: str | None = None
    cancel_reason: str | None = None
    terminal_at: datetime | None = None
    already_terminal: bool = False


class RefreshStatus(BaseModel):
    """Current source-owned refresh state and sanitized latest-run summary."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    resource: str
    policy: str
    config_version: int
    cursor_contract: str
    cursor: dict[str, Any] | None = None
    cursor_observed_at: datetime | None = None
    fencing_token: int
    latest_run: RefreshRunView | None = None
    input_dataset_version_id: str | None = None
    pipeline_run_id: str | None = None
    lag_seconds: int | None = None
    freshness_lag_seconds: int | None = None
    duplicate_count: int = 0
    late_count: int = 0
    retry_count: int = 0
    dlq_count: int = 0
    next_schedule_at: datetime | None = None
    sla_status: str = "not_configured"
    backfill_window_seconds: int = 0


class RefreshScheduleView(BaseModel):
    """Public schedule fields; no source connection details are returned."""

    model_config = ConfigDict(extra="forbid")

    id: str
    target_type: str
    target_id: str
    cron_expr: str
    timezone: str
    business_calendar: list[str] = Field(default_factory=list)
    sla_seconds: int = 0
    retry_policy: dict[str, Any] | None = None
    backfill_window_seconds: int = 0
    max_pending_runs: int = 1
    enabled: bool = True
    next_due_at: datetime | None = None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _source_state(
    db: Session, *, source_id: str, resource: str | None = None,
) -> RefreshSourceState | None:
    query = select(RefreshSourceState).where(RefreshSourceState.source_id == source_id)
    if resource is not None:
        query = query.where(RefreshSourceState.resource == resource)
    return db.execute(
        query.order_by(RefreshSourceState.updated_at.desc(), RefreshSourceState.resource)
    ).scalars().first()


def _source_resource(
    db: Session, *, source_id: str, resource: str | None = None,
) -> str:
    if resource:
        return resource
    state = _source_state(db, source_id=source_id)
    if state is not None:
        return state.resource
    connection = db.get(Connection, source_id)
    if connection is None:
        raise RefreshError("SOURCE_NOT_FOUND", f"no source connection {source_id}")
    raw = dict(connection.config or {})
    # Connection configs are normally encrypted.  A persisted resource hint
    # is optional; credentials are never accepted from this request path.
    if raw.get("_encrypted"):
        try:
            from app.services import encryption_service

            raw = json.loads(encryption_service.decrypt(raw["_encrypted"]))
        except Exception:
            raw = {}
    return str(raw.get("resource") or raw.get("table") or DEFAULT_RESOURCE)


def _connection_configuration(connection: Connection) -> dict[str, Any]:
    raw = dict(connection.config or {})
    encrypted = raw.get("_encrypted")
    if encrypted:
        try:
            from app.services import encryption_service

            raw = json.loads(encryption_service.decrypt(encrypted))
        except Exception:
            raw = {}
    return raw


def _policy_for_source(
    connection: Connection, state: RefreshSourceState | None,
) -> RefreshPolicy:
    configured = (state.configuration or {}).get("refresh_policy") if state is not None else None
    configured = configured or connection.refresh_policy or _connection_configuration(connection).get("refresh_policy")
    try:
        return RefreshPolicy(configured or RefreshPolicy.MICRO_BATCH.value)
    except ValueError as exc:
        raise RefreshError("INVALID_REFRESH_POLICY", "stored source refresh policy is unsupported") from exc


def _validate_backfill(
    db: Session, *, source_id: str, backfill_from: datetime | None,
    backfill_to: datetime | None, now: datetime,
) -> RefreshSchedule | None:
    if backfill_from is None and backfill_to is None:
        return None
    if backfill_from is None or backfill_to is None:
        raise RefreshError("INVALID_BACKFILL_WINDOW", "backfill_from and backfill_to must be provided together")
    backfill_from = _as_utc(backfill_from)
    backfill_to = _as_utc(backfill_to)
    if backfill_to < backfill_from or backfill_to > now:
        raise RefreshError("INVALID_BACKFILL_WINDOW", "backfill window must end at or before now")
    schedule = db.execute(
        select(RefreshSchedule).where(
            RefreshSchedule.target_type == "source",
            RefreshSchedule.target_id == source_id,
        )
    ).scalar_one_or_none()
    limit = schedule.backfill_window_seconds if schedule is not None else 0
    if limit <= 0 or backfill_from < now - timedelta(seconds=limit):
        raise RefreshError("BACKFILL_WINDOW_EXCEEDED", "requested backfill exceeds the persisted schedule window")
    return schedule


def _run_cursor_json(state: RefreshSourceState) -> dict[str, Any] | None:
    return dict(state.cursor_json) if state.cursor_json is not None else None


def trigger_refresh(
    db: Session, *, source_id: str, resource: str, mode: RefreshPolicy,
    backfill_from: datetime | None, backfill_to: datetime | None,
    operator_id: str, now: datetime,
) -> RefreshRun:
    """Persist one idempotent, queued run from server-owned source state."""
    now = _as_utc(now) or _now()
    connection = db.get(Connection, source_id)
    if connection is None:
        raise RefreshError("SOURCE_NOT_FOUND", f"no source connection {source_id}")
    state = _source_state(db, source_id=source_id, resource=resource)
    if state is None:
        state = _lock_or_create_source_state(
            db, source_id=source_id, resource=resource, now=now,
            default_cursor_contract=connection.cursor_contract or "watermark_primary_key",
        )
    else:
        # Serialize the idempotency read with any concurrent manual trigger
        # for this source/resource. The queue message remains run-ID-only.
        state = _lock_source_state(db, source_id=source_id, resource=resource) or state
    if state.cursor_contract not in {"watermark_primary_key", "opaque_source_cursor"}:
        raise RefreshError("INVALID_CURSOR_CONTRACT", "stored source cursor contract is unsupported")
    try:
        policy = mode if isinstance(mode, RefreshPolicy) else RefreshPolicy(mode)
    except ValueError as exc:
        raise RefreshError("INVALID_REFRESH_POLICY", "refresh mode is unsupported") from exc
    configured_policy = _policy_for_source(connection, state)
    if policy is RefreshPolicy.EVENT_DRIVEN:
        raise RefreshError("EVENT_TRIGGER_REQUIRES_SIGNATURE", "event-driven refreshes require the signed webhook route")
    if policy is not configured_policy:
        raise RefreshError(
            "REFRESH_POLICY_MISMATCH",
            "requested refresh mode does not match the persisted source policy",
        )
    _validate_backfill(db, source_id=source_id, backfill_from=backfill_from, backfill_to=backfill_to, now=now)

    backfill_key = ""
    if backfill_from is not None or backfill_to is not None:
        backfill_key = f":{_as_utc(backfill_from).isoformat()}:{_as_utc(backfill_to).isoformat()}"
    idempotency_key = f"manual:{source_id}:{resource}:{policy.value}{backfill_key}"
    existing = db.execute(
        select(RefreshRun).where(
            RefreshRun.source_id == source_id,
            RefreshRun.resource == resource,
            RefreshRun.config_version == state.config_version,
            RefreshRun.idempotency_key == idempotency_key,
        ).order_by(RefreshRun.created_at.desc())
    ).scalars().first()
    if existing is not None:
        db.commit()
        db.refresh(existing)
        return existing

    run = RefreshRun(
        id=str(uuid.uuid4()), source_id=source_id, resource=resource,
        policy=policy.value, trigger="manual", config_version=state.config_version,
        cursor_contract=state.cursor_contract, status="queued", dispatch_state="pending",
        dispatch_queue="refresh.poll", idempotency_key=idempotency_key,
        cursor_before_json=_run_cursor_json(state), fencing_token=0, retry_count=0,
        source_provenance=(
            {"backfill_from": _as_utc(backfill_from).isoformat(), "backfill_to": _as_utc(backfill_to).isoformat()}
            if backfill_from is not None and backfill_to is not None else None
        ),
    )
    db.add(run)
    db.add(RefreshRunTransition(
        id=str(uuid.uuid4()), run_id=run.id, from_status=None,
        to_status="queued", reason="MANUAL_TRIGGER", actor=operator_id,
    ))
    db.commit()
    db.refresh(run)
    return run


def cancel_refresh_run(
    db: Session, *, run_id: str, operator_id: str, reason: str, now: datetime,
) -> RefreshRun:
    """Persist the Task 6 cancellation request and preserve terminal results."""
    return request_refresh_cancellation(
        db, run_id=run_id, requested_by=operator_id, reason=reason, now=_as_utc(now) or _now(),
    )


def _clone_failed_run(db: Session, *, original: RefreshRun, operator_id: str, now: datetime) -> RefreshRun:
    key = f"replay:run:{original.id}"
    existing = db.execute(
        select(RefreshRun).where(
            RefreshRun.source_id == original.source_id,
            RefreshRun.resource == original.resource,
            RefreshRun.idempotency_key == key,
        ).order_by(RefreshRun.created_at.desc())
    ).scalars().first()
    if existing is not None:
        db.commit()
        return existing
    run = RefreshRun(
        id=str(uuid.uuid4()), source_id=original.source_id, resource=original.resource,
        policy=original.policy, trigger="replay", config_version=original.config_version,
        cursor_contract=original.cursor_contract, status="queued", dispatch_state="pending",
        dispatch_queue="refresh.replay", idempotency_key=key,
        cursor_before_json=dict(original.cursor_before_json) if original.cursor_before_json else None,
        fencing_token=0, retry_count=0, retry_reason=f"REPLAY_OF:{original.id}",
    )
    db.add(run)
    db.add(RefreshRunTransition(
        id=str(uuid.uuid4()), run_id=run.id, from_status=None,
        to_status="queued", reason="REPLAY", actor=operator_id,
    ))
    db.commit()
    db.refresh(run)
    return run


def replay_refresh_run(
    db: Session, *, run_id: str, dead_letter_id: str | None,
    operator_id: str, now: datetime,
) -> RefreshRun:
    """Create a new durable replay run without modifying original progress."""
    now = _as_utc(now) or _now()
    original = db.get(RefreshRun, run_id)
    letter = db.get(RefreshDeadLetter, dead_letter_id) if dead_letter_id else None
    if dead_letter_id and letter is None:
        raise RefreshError("DEAD_LETTER_NOT_FOUND", f"no RefreshDeadLetter {dead_letter_id}")
    if letter is None and original is not None:
        letter = db.execute(
            select(RefreshDeadLetter).where(RefreshDeadLetter.run_id == original.id)
            .order_by(RefreshDeadLetter.created_at.desc())
        ).scalars().first()
    if letter is not None:
        if (
            (original is not None and (
                letter.source_id != original.source_id
                or letter.resource != original.resource
                or (letter.run_id is not None and letter.run_id != original.id)
            ))
            or (original is None and letter.run_id is not None and letter.run_id != run_id)
        ):
            raise RefreshError("REPLAY_NOT_ALLOWED", "dead letter is not associated with this refresh run")
        if letter.replay_status == "replayed" and letter.replay_run_id:
            existing = db.get(RefreshRun, letter.replay_run_id)
            if existing is not None:
                db.commit()
                return existing
        if not letter.event_id:
            raise RefreshError("REPLAY_NOT_ALLOWED", "dead letter has no retained event identity")
        new = EventIngestService().replay_dead_letter(
            db, dead_letter_id=letter.id, operator_id=operator_id, now=now,
        )
        # Task 10 reserves a dedicated replay queue.  The event service still
        # owns the retained inbox/DLQ transition; only the task destination is
        # adapted here.
        new.dispatch_queue = "refresh.replay"
        if original is not None and original.cursor_before_json is not None:
            new.cursor_before_json = dict(original.cursor_before_json)
        db.commit()
        db.refresh(new)
        return new
    if original is None:
        raise RefreshError("REFRESH_RUN_NOT_FOUND", f"no RefreshRun {run_id}")
    if original.status not in {"failed", "dead_lettered"}:
        raise RefreshError("REPLAY_NOT_ALLOWED", "only failed or dead-lettered runs may be replayed")
    return _clone_failed_run(db, original=original, operator_id=operator_id, now=now)


def get_last_successful_refresh_run(
    db: Session, *, source_id: str, resource: str | None = None,
) -> RefreshRun | None:
    """The durable pointer Task 20's snapshot freshness materialization
    reads: the last `RefreshRun` `record_refresh_outcome` recorded as
    successful for this (source_id, resource), via the authoritative
    `RefreshSourceState.last_successful_run_id` — never inferred from
    `created_at` ordering, which could return a later failed/dead-lettered
    attempt instead."""
    state = _source_state(db, source_id=source_id, resource=resource)
    if state is None or state.last_successful_run_id is None:
        return None
    return db.get(RefreshRun, state.last_successful_run_id)


def get_refresh_status(
    db: Session, *, source_id: str, resource: str | None = None,
) -> RefreshStatus:
    """Return persisted source/run state only; never trusts request cursors."""
    resource = _source_resource(db, source_id=source_id, resource=resource)
    state = _source_state(db, source_id=source_id, resource=resource)
    connection = db.get(Connection, source_id)
    if connection is None:
        raise RefreshError("SOURCE_NOT_FOUND", f"no source connection {source_id}")
    if state is None:
        state = _lock_or_create_source_state(
            db, source_id=source_id, resource=resource, now=_now(),
            default_cursor_contract=connection.cursor_contract or "watermark_primary_key",
        )
        db.commit()
        db.refresh(state)
    policy = _policy_for_source(connection, state)
    latest = db.execute(
        select(RefreshRun).where(
            RefreshRun.source_id == source_id, RefreshRun.resource == resource,
        ).order_by(RefreshRun.created_at.desc(), RefreshRun.id.desc())
    ).scalars().first()
    latest_view = _run_view(db, latest) if latest is not None else None
    schedule = db.execute(
        select(RefreshSchedule).where(
            RefreshSchedule.target_type == "source", RefreshSchedule.target_id == source_id,
        )
    ).scalar_one_or_none()
    cursor = dict(state.cursor_json) if state.cursor_json is not None else None
    observed_at = None
    if cursor and cursor.get("observed_at"):
        try:
            observed_at = _as_utc(datetime.fromisoformat(str(cursor["observed_at"])))
        except ValueError:
            observed_at = None
    lag = latest.lag_seconds if latest is not None and latest.lag_seconds is not None else None
    if lag is None and observed_at is not None:
        lag = max(0, int((_now() - (_as_utc(observed_at) or _now())).total_seconds()))
    input_id = latest_view.input_dataset_version_ids[0] if latest_view and latest_view.input_dataset_version_ids else None
    pipeline_run_id = latest.pipeline_run_id if latest is not None else None
    sla_status = "not_configured"
    if schedule is not None and schedule.sla_seconds is not None:
        sla_status = "within_sla" if lag is None or lag <= schedule.sla_seconds else "breached"
    return RefreshStatus(
        source_id=source_id, resource=resource, policy=policy.value,
        config_version=state.config_version, cursor_contract=state.cursor_contract,
        cursor=cursor, cursor_observed_at=observed_at, fencing_token=state.fencing_token,
        latest_run=latest_view, input_dataset_version_id=input_id,
        pipeline_run_id=pipeline_run_id, lag_seconds=lag,
        freshness_lag_seconds=lag, duplicate_count=(latest.duplicate_count or 0) if latest else 0,
        late_count=(latest.late_count or 0) if latest else 0,
        retry_count=(latest.retry_count or 0) if latest else 0,
        dlq_count=db.query(RefreshDeadLetter).filter(
            RefreshDeadLetter.source_id == source_id, RefreshDeadLetter.resource == resource,
        ).count(),
        next_schedule_at=_as_utc(schedule.next_due_at) if schedule is not None else None,
        sla_status=sla_status,
        backfill_window_seconds=(schedule.backfill_window_seconds or 0) if schedule else 0,
    )


def _run_view(db: Session, run: RefreshRun) -> RefreshRunView:
    ids = list(db.execute(
        select(PipelineRunInput.dataset_version_id)
        .where(PipelineRunInput.pipeline_run_id == run.pipeline_run_id)
        .order_by(PipelineRunInput.input_ordinal)
    ).scalars()) if run.pipeline_run_id else []
    return RefreshRunView(
        run_id=run.id, source_id=run.source_id, resource=run.resource,
        policy=run.policy, trigger=run.trigger, status=run.status,
        dispatch_state=run.dispatch_state, dispatch_queue=run.dispatch_queue,
        config_version=run.config_version, cursor_contract=run.cursor_contract,
        cursor_before=dict(run.cursor_before_json) if run.cursor_before_json else None,
        cursor_after=dict(run.cursor_after_json) if run.cursor_after_json else None,
        input_dataset_version_ids=ids, pipeline_run_id=run.pipeline_run_id,
        lag_seconds=run.lag_seconds, duplicate_count=run.duplicate_count or 0,
        late_count=run.late_count or 0, retry_count=run.retry_count or 0,
        retry_reason=run.retry_reason,
        dead_letter_id=(
            db.execute(
                select(RefreshDeadLetter.id)
                .where(RefreshDeadLetter.run_id == run.id)
                .order_by(RefreshDeadLetter.created_at.desc())
            ).scalars().first()
        ),
        replay_status=(
            db.execute(
                select(RefreshDeadLetter.replay_status)
                .where(RefreshDeadLetter.run_id == run.id)
                .order_by(RefreshDeadLetter.created_at.desc())
            ).scalars().first()
        ),
        cancel_requested_at=run.cancel_requested_at,
        cancel_requested_by=run.cancel_requested_by,
        cancel_reason=run.cancel_reason, terminal_at=run.terminal_at,
        already_terminal=bool(getattr(run, "already_terminal", False)),
    )


def schedule_view(schedule: RefreshSchedule) -> RefreshScheduleView:
    return RefreshScheduleView(
        id=schedule.id, target_type=schedule.target_type, target_id=schedule.target_id,
        cron_expr=schedule.cron_expression, timezone=schedule.timezone,
        business_calendar=list(schedule.excluded_dates or []),
        sla_seconds=schedule.sla_seconds or 0,
        retry_policy=dict(schedule.retry_policy) if schedule.retry_policy else None,
        backfill_window_seconds=schedule.backfill_window_seconds or 0,
        max_pending_runs=schedule.max_pending_runs, enabled=bool(schedule.enabled),
        next_due_at=_as_utc(schedule.next_due_at),
    )


def get_refresh_schedule(db: Session, *, source_id: str) -> RefreshSchedule:
    schedule = db.execute(
        select(RefreshSchedule).where(
            RefreshSchedule.target_type == "source",
            RefreshSchedule.target_id == source_id,
        )
    ).scalars().first()
    if schedule is None:
        raise RefreshError("SCHEDULE_NOT_FOUND", f"no schedule for source {source_id}")
    return schedule


def update_refresh_schedule(
    db: Session, *, source_id: str, cron_expr: str, timezone_name: str = "UTC",
    business_calendar: list[str] | None = None, sla_seconds: int = 0,
    retry_policy: Mapping[str, object] | None = None,
    backfill_window_seconds: int = 0, max_pending_runs: int = 1,
    enabled: bool = True, now: datetime | None = None,
) -> RefreshSchedule:
    if db.get(Connection, source_id) is None:
        raise RefreshError("SOURCE_NOT_FOUND", f"no source connection {source_id}")
    return upsert_refresh_schedule(
        db,
        ScheduleRequest(
            target_type="connection", target_id=source_id, cron_expr=cron_expr,
            timezone=timezone_name, business_calendar=business_calendar or [],
            sla_seconds=sla_seconds, retry_policy=retry_policy,
            backfill_window_seconds=backfill_window_seconds,
            max_pending_runs=max_pending_runs, enabled=enabled,
        ),
        now=_as_utc(now) or _now(),
    )


def get_refresh_health(
    db: Session, *, now: datetime, observations: Mapping[str, QueueObservation],
) -> RefreshOperability:
    return collect_refresh_operability(db, now=_as_utc(now) or _now(), observations=observations)


__all__ = [
    "RefreshRunView", "RefreshStatus", "RefreshScheduleView", "get_refresh_schedule",
    "trigger_refresh", "cancel_refresh_run", "replay_refresh_run",
    "get_last_successful_refresh_run", "get_refresh_status", "get_refresh_health",
    "schedule_view", "update_refresh_schedule",
]
