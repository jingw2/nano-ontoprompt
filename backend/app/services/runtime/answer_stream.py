"""Ephemeral live preview of the model's answer while a Turn is in flight.

Deliberately NOT part of the authoritative `agent_runtime_events` log (see
`app.services.runtime.events`): that log is persisted in one fenced
transaction only once the whole Turn finishes, so it can never show live
progress. This is best-effort UI sugar the frontend polls while a Turn
runs — safe to lose on a worker crash/retry, since the real answer is
always the persisted `final_response` event once the Turn actually
completes.
"""
from __future__ import annotations

import json
from typing import Any, Callable

TTL_SECONDS = 600
DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS = 0.5


def _default_redis_client(redis_url: str):
    import redis

    return redis.Redis.from_url(
        redis_url, decode_responses=True,
        socket_connect_timeout=DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_timeout=DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS,
    )


def _client(redis_url: str | None, redis_client_factory: Callable[[], Any] | None):
    if redis_client_factory is not None:
        return redis_client_factory()
    if redis_url is None:
        from app.config import settings

        redis_url = settings.redis_url
    return _default_redis_client(redis_url)


def _key(turn_id: str) -> str:
    return f"agent_turn_answer:{turn_id}"


def publish_delta(
    turn_id: str, text: str, *, redis_url: str | None = None,
    redis_client_factory: Callable[[], Any] | None = None,
) -> None:
    """Best-effort — a Redis hiccup must never fail the Turn, so failures
    are swallowed silently here; callers do not need their own try/except."""
    try:
        client = _client(redis_url, redis_client_factory)
        client.set(_key(turn_id), json.dumps({"text": text}, ensure_ascii=False), ex=TTL_SECONDS)
    except Exception:
        return


def read_delta(
    turn_id: str, *, redis_url: str | None = None,
    redis_client_factory: Callable[[], Any] | None = None,
) -> dict | None:
    try:
        client = _client(redis_url, redis_client_factory)
        raw = client.get(_key(turn_id))
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None
