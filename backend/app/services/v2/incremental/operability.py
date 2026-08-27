"""Refresh queue/backpressure/readiness collector (Task 6A).

Combines sanitized broker observations (`QueueObservation` — queue depth,
oldest-queued age, worker-ready; a broker inspector supplies these only
after discarding message bodies) with durable `RefreshRun` state
(retry/dead-letter counts and run lag) into one read-only operability
snapshot. Never calls a connector or broker with source credentials, and
never persists or forwards a message payload.

`RefreshRun` has no queue-attributing column yet: `refresh.poll` and
`refresh.event` are attributed by `RefreshPolicy` (batch/micro_batch vs.
event_driven — the only durable signal that already exists), and
`refresh.schedule`/`refresh.replay` report zero/`None` run-level counts
until a later task (7/10) adds an explicit attribution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.v2.refresh import RefreshRun
from app.schemas.refresh import RefreshPolicy
from app.tasks.topology import (
    QUEUE_REFRESH_EVENT,
    QUEUE_REFRESH_POLL,
    QUEUE_REFRESH_REPLAY,
    QUEUE_REFRESH_SCHEDULE,
)

REFRESH_QUEUE_NAMES = (QUEUE_REFRESH_SCHEDULE, QUEUE_REFRESH_POLL, QUEUE_REFRESH_EVENT, QUEUE_REFRESH_REPLAY)

_POLICIES_BY_QUEUE: dict[str, tuple[str, ...]] = {
    QUEUE_REFRESH_POLL: (RefreshPolicy.BATCH.value, RefreshPolicy.MICRO_BATCH.value),
    QUEUE_REFRESH_EVENT: (RefreshPolicy.EVENT_DRIVEN.value,),
}


@dataclass(frozen=True)
class QueueObservation:
    """A sanitized broker observation for one queue — no message body."""

    queue: str
    depth: int
    oldest_queued_age_seconds: float | None
    worker_ready: bool


@dataclass(frozen=True)
class RefreshQueueMetric:
    queue: str
    depth: int
    oldest_queued_age_seconds: float | None
    running_count: int
    retry_count: int
    dead_letter_count: int
    max_run_lag_seconds: float | None


@dataclass(frozen=True)
class RefreshReadiness:
    database_ready: bool
    broker_ready: bool
    refresh_worker_ready: bool

    @property
    def ready(self) -> bool:
        return self.database_ready and self.broker_ready and self.refresh_worker_ready


@dataclass(frozen=True)
class RefreshOperability:
    queues: tuple[RefreshQueueMetric, ...]
    readiness: RefreshReadiness
    observed_at: datetime


def _database_ready(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _queue_run_metrics(db: Session, queue: str) -> tuple[int, int, int, float | None]:
    policies = _POLICIES_BY_QUEUE.get(queue)
    if not policies:
        return (0, 0, 0, None)

    running_count = db.execute(
        select(func.count()).select_from(RefreshRun)
        .where(RefreshRun.policy.in_(policies), RefreshRun.status == "running")
    ).scalar_one()
    retry_count = db.execute(
        select(func.count()).select_from(RefreshRun)
        .where(RefreshRun.policy.in_(policies), RefreshRun.status == "queued", RefreshRun.retry_count > 0)
    ).scalar_one()
    dead_letter_count = db.execute(
        select(func.count()).select_from(RefreshRun)
        .where(RefreshRun.policy.in_(policies), RefreshRun.status == "dead_lettered")
    ).scalar_one()
    max_lag = db.execute(
        select(func.max(RefreshRun.lag_seconds))
        .where(RefreshRun.policy.in_(policies), RefreshRun.lag_seconds.isnot(None))
    ).scalar_one()
    return (running_count, retry_count, dead_letter_count, max_lag)


def collect_refresh_operability(
    db: Session, *, now: datetime, observations: Mapping[str, QueueObservation],
) -> RefreshOperability:
    """Validate `observations` covers every named refresh queue, then derive
    retry/DLQ/run-lag counts only from durable refresh state."""
    missing = [queue for queue in REFRESH_QUEUE_NAMES if queue not in observations]
    if missing:
        raise ValueError(f"missing queue observations for: {missing}")

    queue_metrics = []
    for queue in REFRESH_QUEUE_NAMES:
        observation = observations[queue]
        running_count, retry_count, dead_letter_count, max_lag = _queue_run_metrics(db, queue)
        queue_metrics.append(RefreshQueueMetric(
            queue=queue,
            depth=observation.depth,
            oldest_queued_age_seconds=observation.oldest_queued_age_seconds,
            running_count=running_count,
            retry_count=retry_count,
            dead_letter_count=dead_letter_count,
            max_run_lag_seconds=max_lag,
        ))

    readiness = RefreshReadiness(
        database_ready=_database_ready(db),
        broker_ready=all(observations[queue].depth >= 0 for queue in REFRESH_QUEUE_NAMES),
        refresh_worker_ready=all(observations[queue].worker_ready for queue in REFRESH_QUEUE_NAMES),
    )
    return RefreshOperability(queues=tuple(queue_metrics), readiness=readiness, observed_at=now)
