"""Sanitized broker and worker observations for refresh operability.

Celery's Redis transport stores task bodies in Redis lists.  This observer
uses Kombu's queue-size operation and a separate, short-lived sorted-set
index of durable run IDs/timestamps, so health checks never read or return a
task body.  Worker readiness is based on both a successful worker ping and
the worker's declared queue bindings; an unavailable broker or worker is
represented by ``depth=-1`` / ``worker_ready=False``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

import redis

from app.services.v2.incremental.operability import QueueObservation


OBSERVATION_KEY_PREFIX = "ontexus:refresh:queue:"
OBSERVATION_KEY_SUFFIX = ":enqueued"
OBSERVATION_TTL_SECONDS = 24 * 60 * 60
DEFAULT_WORKER_INSPECT_TIMEOUT_SECONDS = 1.0
DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS = 0.5


def observation_key(queue: str) -> str:
    """Return the Redis key used for sanitized enqueue timestamps."""
    return f"{OBSERVATION_KEY_PREFIX}{queue}{OBSERVATION_KEY_SUFFIX}"


def _default_redis_client(redis_url: str):
    return redis.Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_timeout=DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS,
    )


def record_refresh_enqueue(
    *, queue: str, run_id: str, at: datetime | None = None,
    redis_url: str | None = None,
    redis_client_factory: Callable[[], Any] | None = None,
) -> None:
    """Record only a run ID and enqueue timestamp for oldest-age metrics.

    This is best effort operational metadata.  Broker publication remains
    authoritative for dispatch success; an observation-index outage must not
    turn a successfully published run into an API failure.
    """
    if not run_id:
        return
    try:
        if redis_client_factory is None:
            if redis_url is None:
                from app.config import settings

                redis_url = settings.redis_url
            redis_client_factory = lambda: _default_redis_client(redis_url)
        timestamp = (at or datetime.now(timezone.utc)).timestamp()
        client = redis_client_factory()
        key = observation_key(queue)
        client.zadd(key, {run_id: timestamp})
        client.expire(key, OBSERVATION_TTL_SECONDS)
    except Exception:
        # Do not log the exception: Redis connection errors can contain a
        # configured URL, which is operationally sensitive.
        return


def record_refresh_dequeue(
    *, queue: str, run_id: str, redis_url: str | None = None,
    redis_client_factory: Callable[[], Any] | None = None,
) -> None:
    """Remove a consumed run ID from the sanitized queue-age index."""
    if not run_id:
        return
    try:
        if redis_client_factory is None:
            if redis_url is None:
                from app.config import settings

                redis_url = settings.redis_url
            redis_client_factory = lambda: _default_redis_client(redis_url)
        redis_client_factory().zrem(observation_key(queue), run_id)
    except Exception:
        return


class RedisRefreshBrokerObserver:
    """Read named Celery/Redis queue and worker state without task bodies."""

    def __init__(
        self, *, celery_app: Any | None = None, redis_url: str | None = None,
        redis_client_factory: Callable[[], Any] | None = None,
        now_factory: Callable[[], datetime] | None = None,
        worker_timeout: float = DEFAULT_WORKER_INSPECT_TIMEOUT_SECONDS,
    ) -> None:
        if redis_url is None:
            from app.config import settings

            redis_url = settings.redis_url
        if celery_app is None:
            from app.tasks.celery_app import celery_app as configured_celery_app

            celery_app = configured_celery_app
        self.celery_app = celery_app
        self.redis_url = redis_url
        self.redis_client_factory = redis_client_factory or (
            lambda: _default_redis_client(redis_url)
        )
        self.now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self.worker_timeout = worker_timeout

    def _queue_depths(self, queues: Sequence[str]) -> dict[str, int]:
        depths = {queue: -1 for queue in queues}
        try:
            with self.celery_app.connection_for_read() as connection:
                channel = connection.channel()
                try:
                    for queue in queues:
                        try:
                            depth = int(channel._size(queue))
                        except Exception:
                            depth = -1
                        depths[queue] = depth if depth >= 0 else -1
                finally:
                    channel.close()
        except Exception:
            return depths
        return depths

    def _worker_bindings(self) -> Mapping[str, bool]:
        """Return queue readiness from ping plus declared queue bindings."""
        try:
            inspector = self.celery_app.control.inspect(timeout=self.worker_timeout)
            pings = inspector.ping() or {}
            if not pings:
                return {}
            active_queues = inspector.active_queues() or {}
            if not active_queues:
                return {}
            bound_queues: set[str] = set()
            for worker_name, entries in active_queues.items():
                if worker_name not in pings:
                    continue
                for entry in entries or ():
                    queue = entry.get("name") if isinstance(entry, dict) else None
                    if queue:
                        bound_queues.add(str(queue))
            return {queue: queue in bound_queues for queue in bound_queues}
        except Exception:
            return {}

    def _oldest_age(
        self, client: Any, *, queue: str, now: datetime,
    ) -> float | None:
        try:
            entries = client.zrange(observation_key(queue), 0, 0, withscores=True)
            if not entries:
                return None
            score = float(entries[0][1])
            return max(0.0, now.timestamp() - score)
        except Exception:
            return None

    def observe(self, queues: Sequence[str]) -> dict[str, QueueObservation]:
        """Return one sanitized observation for every requested queue."""
        requested = tuple(queues)
        depths = self._queue_depths(requested)
        workers = self._worker_bindings()
        now = self.now_factory()
        try:
            redis_client = self.redis_client_factory()
        except Exception:
            redis_client = None

        observations: dict[str, QueueObservation] = {}
        for queue in requested:
            depth = depths[queue]
            observations[queue] = QueueObservation(
                queue=queue,
                depth=depth,
                oldest_queued_age_seconds=(
                    self._oldest_age(redis_client, queue=queue, now=now)
                    if redis_client is not None and depth > 0 else None
                ),
                worker_ready=bool(workers.get(queue, False)),
            )
        return observations


__all__ = [
    "RedisRefreshBrokerObserver",
    "observation_key",
    "record_refresh_dequeue",
    "record_refresh_enqueue",
]
