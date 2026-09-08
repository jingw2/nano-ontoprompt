"""Refresh queue/backpressure/readiness collector tests (Task 6A).

`collect_refresh_operability` combines sanitized broker `QueueObservation`s
(depth/oldest-age/worker-ready — never a message body) with durable
`RefreshRun`/dead-letter state (retry/DLQ/run-lag counts) — it never touches
a connector or broker with source credentials, and the returned metrics
carry no payload/credential field.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.v2.refresh import RefreshRun
from app.schemas.refresh import RefreshPolicy
from app.services.v2.incremental.contract import claim_refresh_run, mark_refresh_retryable
from app.services.v2.incremental.operability import QueueObservation, collect_refresh_operability

FIXED_NOW = datetime(2026, 8, 26, 0, 0, 0, tzinfo=timezone.utc)


def fixture_queue_observations() -> dict[str, QueueObservation]:
    return {
        "refresh.schedule": QueueObservation(queue="refresh.schedule", depth=0, oldest_queued_age_seconds=None, worker_ready=True),
        "refresh.poll": QueueObservation(queue="refresh.poll", depth=3, oldest_queued_age_seconds=12.5, worker_ready=True),
        "refresh.event": QueueObservation(queue="refresh.event", depth=0, oldest_queued_age_seconds=None, worker_ready=True),
        "refresh.replay": QueueObservation(queue="refresh.replay", depth=1, oldest_queued_age_seconds=200.0, worker_ready=True),
    }


def test_operability_has_queue_age_lag_retry_dlq_and_readiness_without_payloads(db):
    result = collect_refresh_operability(db, now=FIXED_NOW, observations=fixture_queue_observations())
    assert result.readiness.ready is True
    assert {item.queue for item in result.queues} == {"refresh.schedule", "refresh.poll", "refresh.event", "refresh.replay"}
    assert all(item.oldest_queued_age_seconds is None or item.oldest_queued_age_seconds >= 0 for item in result.queues)
    assert all("payload" not in item.__dict__ and "credential" not in item.__dict__ for item in result.queues)


def test_operability_validates_observations_cover_every_refresh_queue():
    incomplete = {"refresh.poll": QueueObservation(queue="refresh.poll", depth=0, oldest_queued_age_seconds=None, worker_ready=True)}
    with pytest.raises(ValueError):
        collect_refresh_operability(None, now=FIXED_NOW, observations=incomplete)


def test_operability_derives_retry_and_dead_letter_counts_from_durable_run_state(db):
    run = claim_refresh_run(
        db, source_id="source-001", resource="orders", policy=RefreshPolicy.BATCH,
        idempotency_key="idem-001", lease_owner="worker-1", now=FIXED_NOW,
    )
    mark_refresh_retryable(db, run_id=run.id, reason="WORKER_INTERRUPTED", now=FIXED_NOW)

    result = collect_refresh_operability(db, now=FIXED_NOW, observations=fixture_queue_observations())
    poll_metric = next(item for item in result.queues if item.queue == "refresh.poll")
    assert poll_metric.retry_count == 1
    assert poll_metric.depth == 3
    assert poll_metric.oldest_queued_age_seconds == 12.5

    schedule_metric = next(item for item in result.queues if item.queue == "refresh.schedule")
    assert schedule_metric.retry_count == 0
    assert schedule_metric.dead_letter_count == 0


def test_operability_uses_durable_queue_attribution_for_schedule_and_replay_runs(db):
    for queue in ("refresh.schedule", "refresh.replay"):
        db.add(RefreshRun(
            id=str(uuid.uuid4()), source_id=f"source-{queue}", resource="orders",
            policy=RefreshPolicy.BATCH.value, trigger="scheduled", config_version=1,
            cursor_contract="watermark_primary_key", status="running",
            dispatch_state="dispatched", dispatch_queue=queue,
            idempotency_key=f"{queue}:{uuid.uuid4()}", fencing_token=0,
        ))
    db.commit()

    result = collect_refresh_operability(db, now=FIXED_NOW, observations=fixture_queue_observations())
    schedule_metric = next(item for item in result.queues if item.queue == "refresh.schedule")
    poll_metric = next(item for item in result.queues if item.queue == "refresh.poll")
    replay_metric = next(item for item in result.queues if item.queue == "refresh.replay")
    assert schedule_metric.running_count == 1
    assert poll_metric.running_count == 0
    assert replay_metric.running_count == 1
