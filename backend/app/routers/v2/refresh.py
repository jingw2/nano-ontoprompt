"""Authenticated refresh operations API.

Routes in this module stop at the durable database/broker boundary. Source
pulls, event materialization, replay work, and cancellation safe points run
only in the named Celery workers.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_editor
from app.models.user import User
from app.schemas.refresh import RefreshError, RefreshPolicy
from app.services.v2.incremental.operations import (
    RefreshRunView,
    RefreshScheduleView,
    RefreshStatus,
    cancel_refresh_run,
    get_refresh_health,
    get_refresh_status,
    replay_refresh_run,
    schedule_view,
    trigger_refresh,
    update_refresh_schedule,
)
from app.services.v2.incremental.operability import QueueObservation
from app.tasks.topology import (
    QUEUE_REFRESH_EVENT,
    QUEUE_REFRESH_POLL,
    QUEUE_REFRESH_REPLAY,
    QUEUE_REFRESH_SCHEDULE,
    build_refresh_dispatch_message,
    enqueue_refresh_run,
)


router = APIRouter()


class RefreshTriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RefreshPolicy
    backfill_from: datetime | None = None
    backfill_to: datetime | None = None


class CancelRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)


class ReplayRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dead_letter_id: str | None = None


class RefreshScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cron_expr: str
    timezone: str = "UTC"
    business_calendar: list[str] = Field(default_factory=list)
    sla_seconds: int = Field(default=0, ge=0)
    retry_policy: dict[str, Any] | None = None
    backfill_window_seconds: int = Field(default=0, ge=0)
    max_pending_runs: int = Field(default=1, ge=1)
    enabled: bool = True


def _http_error(exc: RefreshError) -> HTTPException:
    status = 404 if exc.reason_code.endswith("NOT_FOUND") or exc.reason_code == "SOURCE_NOT_FOUND" else 422
    return HTTPException(status_code=status, detail=exc.reason_code)


def _send_task(task_name: str, args, queue: str) -> str:
    """Send only the durable run ID through Celery's named queue."""
    from app.tasks.celery_app import celery_app

    result = celery_app.send_task(task_name, args=list(args), queue=queue)
    return result.id


def _dispatch_run(db: Session, *, run, task_name: str) -> None:
    """Publish after the caller has committed the run; retain queued state on failure."""
    if run.dispatch_state == "dispatched":
        return
    message = build_refresh_dispatch_message(run_id=run.id, task_name=task_name)
    try:
        enqueue_refresh_run(message=message, send_task=_send_task)
    except Exception:
        # Broker saturation/unavailability is an operability condition, not
        # an API failure. The committed run remains recoverable by the
        # scheduler/reconciler and no connector work runs inline.
        run.dispatch_state = "publish_failed"
        run.dispatch_reason = "PUBLISH_FAILED"
        db.commit()
        return
    run.dispatch_state = "dispatched"
    run.dispatch_reason = None
    db.commit()


def _queue_observations() -> dict[str, QueueObservation]:
    """Return sanitized queue readiness observations without message bodies."""
    return {
        queue: QueueObservation(
            queue=queue, depth=0, oldest_queued_age_seconds=None, worker_ready=True,
        )
        for queue in (
            QUEUE_REFRESH_SCHEDULE,
            QUEUE_REFRESH_POLL,
            QUEUE_REFRESH_EVENT,
            QUEUE_REFRESH_REPLAY,
        )
    }


@router.get("/sources/{source_id}/status", response_model=RefreshStatus)
def refresh_status(
    source_id: str, resource: str | None = None,
    db: Session = Depends(get_db), _: User = Depends(require_editor),
):
    try:
        return get_refresh_status(db, source_id=source_id, resource=resource)
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.post("/sources/{source_id}/run", response_model=RefreshRunView, status_code=202)
def run_refresh(
    source_id: str, body: RefreshTriggerRequest,
    db: Session = Depends(get_db), current_user: User = Depends(require_editor),
):
    from app.services.v2.incremental.operations import _run_view, _source_resource

    try:
        resource = _source_resource(db, source_id=source_id)
        run = trigger_refresh(
            db, source_id=source_id, resource=resource, mode=body.mode,
            backfill_from=body.backfill_from, backfill_to=body.backfill_to,
            operator_id=current_user.id, now=datetime.now(timezone.utc),
        )
        # The durable commit happens inside trigger_refresh before this call.
        _dispatch_run(db, run=run, task_name="refresh.poll")
        db.refresh(run)
        return _run_view(db, run)
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/cancel")
def cancel_refresh(
    run_id: str, body: CancelRefreshRequest,
    db: Session = Depends(get_db), current_user: User = Depends(require_editor),
):
    try:
        run = cancel_refresh_run(
            db, run_id=run_id, operator_id=current_user.id,
            reason=body.reason, now=datetime.now(timezone.utc),
        )
    except RefreshError as exc:
        raise _http_error(exc) from exc
    if getattr(run, "already_terminal", False):
        return JSONResponse(
            status_code=200,
            content={"status": run.status, "already_terminal": True},
        )
    from app.services.v2.incremental.operations import _run_view

    return JSONResponse(status_code=202, content=_run_view(db, run).model_dump(mode="json"))


@router.post("/runs/{run_id}/replay", response_model=RefreshRunView, status_code=202)
def replay_refresh(
    run_id: str, body: ReplayRefreshRequest,
    db: Session = Depends(get_db), current_user: User = Depends(require_editor),
):
    from app.services.v2.incremental.operations import _run_view

    try:
        run = replay_refresh_run(
            db, run_id=run_id, dead_letter_id=body.dead_letter_id,
            operator_id=current_user.id, now=datetime.now(timezone.utc),
        )
        _dispatch_run(db, run=run, task_name="refresh.replay")
        db.refresh(run)
        return _run_view(db, run)
    except RefreshError as exc:
        raise _http_error(exc) from exc


@router.put("/sources/{source_id}/schedule", response_model=RefreshScheduleView)
def update_schedule(
    source_id: str, body: RefreshScheduleRequest,
    db: Session = Depends(get_db), _: User = Depends(require_editor),
):
    try:
        schedule = update_refresh_schedule(
            db, source_id=source_id, cron_expr=body.cron_expr,
            timezone_name=body.timezone, business_calendar=body.business_calendar,
            sla_seconds=body.sla_seconds, retry_policy=body.retry_policy,
            backfill_window_seconds=body.backfill_window_seconds,
            max_pending_runs=body.max_pending_runs, enabled=body.enabled,
        )
        return schedule_view(schedule)
    except RefreshError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/health")
def refresh_health(
    db: Session = Depends(get_db), _: User = Depends(require_editor),
):
    result = get_refresh_health(
        db, now=datetime.now(timezone.utc), observations=_queue_observations(),
    )
    return {
        "queues": [asdict(item) for item in result.queues],
        "readiness": asdict(result.readiness),
        "observed_at": result.observed_at.isoformat(),
    }


__all__ = [
    "router", "RefreshTriggerRequest", "CancelRefreshRequest",
    "ReplayRefreshRequest", "RefreshScheduleRequest",
]
