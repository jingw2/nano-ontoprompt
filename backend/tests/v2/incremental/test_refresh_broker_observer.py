"""Tests for sanitized Redis/Celery refresh queue observations."""
from __future__ import annotations

from datetime import datetime, timezone

from app.services.v2.incremental.broker_observer import RedisRefreshBrokerObserver


QUEUES = ("refresh.schedule", "refresh.poll", "refresh.event", "refresh.replay")


class FakeRedis:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def zrange(self, key, start, end, *, withscores):
        self.calls.append((key, start, end, withscores))
        score = self.scores.get(key)
        return [] if score is None else [("opaque-run-id", score)]


class FakeChannel:
    def __init__(self, depths):
        self.depths = depths

    def _size(self, queue):
        return self.depths[queue]

    def close(self):
        return None


class FakeConnection:
    def __init__(self, channel):
        self.channel_instance = channel

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def channel(self):
        return self.channel_instance


class FakeInspector:
    def __init__(self, workers, active_queues):
        self.workers = workers
        self.active_queues_result = active_queues

    def ping(self):
        return self.workers

    def active_queues(self):
        return self.active_queues_result


class FakeControl:
    def __init__(self, inspector):
        self.inspector = inspector

    def inspect(self, *, timeout):
        assert timeout > 0
        return self.inspector


class FakeCelery:
    def __init__(self, depths, workers, active_queues):
        self.connection = FakeConnection(FakeChannel(depths))
        self.control = FakeControl(FakeInspector(workers, active_queues))

    def connection_for_read(self):
        return self.connection


def test_observer_reports_real_depth_stale_age_and_queue_bound_worker():
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    redis_client = FakeRedis({
        "ontexus:refresh:queue:refresh.poll:enqueued": now.timestamp() - 321.5,
    })
    celery = FakeCelery(
        {queue: (3 if queue == "refresh.poll" else 0) for queue in QUEUES},
        {"refresh-poll@worker": {"ok": "pong"}},
        {"refresh-poll@worker": [{"name": "refresh.poll"}]},
    )
    observer = RedisRefreshBrokerObserver(
        celery_app=celery, redis_client_factory=lambda: redis_client,
        now_factory=lambda: now,
    )

    observations = observer.observe(QUEUES)

    assert observations["refresh.poll"].depth == 3
    assert observations["refresh.poll"].oldest_queued_age_seconds == 321.5
    assert observations["refresh.poll"].worker_ready is True
    assert observations["refresh.schedule"].depth == 0
    assert observations["refresh.schedule"].oldest_queued_age_seconds is None
    assert all("opaque-run-id" not in str(observation) for observation in observations.values())


def test_observer_marks_worker_unavailable_without_leaking_broker_data():
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    redis_client = FakeRedis({
        "ontexus:refresh:queue:refresh.poll:enqueued": now.timestamp() - 600,
    })
    celery = FakeCelery(
        {queue: 2 for queue in QUEUES}, {}, {},
    )
    observer = RedisRefreshBrokerObserver(
        celery_app=celery, redis_client_factory=lambda: redis_client,
        now_factory=lambda: now,
    )

    observations = observer.observe(QUEUES)

    assert all(observation.depth == 2 for observation in observations.values())
    assert observations["refresh.poll"].oldest_queued_age_seconds == 600
    assert all(observation.worker_ready is False for observation in observations.values())
