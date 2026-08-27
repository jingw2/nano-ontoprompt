"""Celery refresh tasks (Task 7) — beat-driven schedule dispatch and the
run-ID-only `refresh.connection`/`refresh.pipeline` task bodies.

`dispatch_due_schedules_task` (`refresh.schedule` queue) is the only Celery
entry point that turns a persisted `RefreshSchedule` into a durable
`RefreshRun` and hands its `run_id` to the broker — see
`app.services.v2.scheduler.schedule_service`. `refresh_connection_task`/
`refresh_pipeline_task` (`refresh.poll` queue) are the run-ID-only bodies a
worker executes once it picks a dispatched run off that queue; the actual
connector pull/poll logic that turns a claimed run into a completed refresh
is a later task's concern (Task 8) — this module only wires the durable
run_id through to that not-yet-built execution step and logs receipt, so
the Celery task names/queues/routing this task's brief requires are real
and registered now.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.tasks.celery_app import celery_app
from app.tasks.topology import QUEUE_REFRESH_POLL, enqueue_refresh_run

logger = logging.getLogger(__name__)

# Placeholder resource scope for a connection/pipeline-level manual or
# scheduled refresh until multi-resource fan-out lands (matches
# schedule_service.DEFAULT_RESOURCE).
DEFAULT_RESOURCE = "__scheduled__"


def _celery_send_task(task_name: str, args, queue: str) -> str:
    """Adapter matching `enqueue_refresh_run`'s `send_task` contract —
    Celery's own `send_task(name, args, kwargs, ...)` would otherwise bind a
    positional third argument to `kwargs`, not `queue`."""
    result = celery_app.send_task(task_name, args=list(args), queue=queue)
    return result.id


def _send_refresh_via_celery(message) -> str:
    return enqueue_refresh_run(message=message, send_task=_celery_send_task)


@celery_app.task(name="refresh.dispatch_due_schedules")
def dispatch_due_schedules_task() -> list[str]:
    """Beat-driven: publish every due persisted schedule's run to the broker.
    Never pulls a connector itself — only claims/creates the durable
    `RefreshRun` and hands its run_id to `refresh.connection`/`refresh.pipeline`."""
    import app.models  # noqa: F401 — register all tables for the worker subprocess
    from app.database import SessionLocal
    from app.services.v2.scheduler.schedule_service import dispatch_due_schedules

    db = SessionLocal()
    try:
        return dispatch_due_schedules(
            db, now=datetime.now(timezone.utc), lease_owner="beat",
            send_refresh=_send_refresh_via_celery,
        )
    finally:
        db.close()


@celery_app.task(name="refresh.connection")
def refresh_connection_task(run_id: str) -> dict:
    """Run-ID-only connection refresh body (`refresh.poll` queue). Reloads
    nothing but the run_id here — connector execution is a later task."""
    logger.info("refresh.connection received run_id=%s (connector execution lands in a later task)", run_id)
    return {"run_id": run_id, "status": "accepted"}


@celery_app.task(name="refresh.pipeline")
def refresh_pipeline_task(run_id: str) -> dict:
    """Run-ID-only pipeline refresh body (`refresh.poll` queue)."""
    logger.info("refresh.pipeline received run_id=%s (connector execution lands in a later task)", run_id)
    return {"run_id": run_id, "status": "accepted"}


# ── manual on-demand trigger (shared by the /connections/{id}/sync route and
# the sync_connection/sync_all_connections compatibility wrappers) ─────────

def create_manual_connection_run(db, connection_id: str):
    """Claim a durable, immediately-leased `RefreshRun` for a manual
    on-demand connection sync (Task 6 contract, BATCH policy) — distinct
    from a T+1-scheduled run, which starts `queued` with no lease until a
    worker claims it."""
    from app.schemas.refresh import RefreshPolicy
    from app.services.v2.incremental.contract import claim_refresh_run

    run = claim_refresh_run(
        db, source_id=connection_id, resource=DEFAULT_RESOURCE, policy=RefreshPolicy.BATCH,
        idempotency_key=f"manual:{uuid.uuid4()}", lease_owner="manual-sync",
        now=datetime.now(timezone.utc),
    )
    run.dispatch_queue = QUEUE_REFRESH_POLL
    db.commit()
    db.refresh(run)
    return run


def trigger_connection_refresh(connection_id: str) -> dict:
    """sync_connection/sync_all_connections compatibility wrapper: claim a
    manual run and dispatch it through `refresh.connection` on `refresh.poll`."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        run = create_manual_connection_run(db, connection_id)
    finally:
        db.close()

    try:
        refresh_connection_task.delay(run.id)
        return {"connection_id": connection_id, "run_id": run.id, "status": "queued"}
    except Exception as exc:
        return {"connection_id": connection_id, "run_id": run.id, "status": "dispatch_failed", "error": str(exc)}
