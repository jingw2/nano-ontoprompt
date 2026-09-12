"""Ephemeral live-answer-preview channel (see `app.services.runtime.
answer_stream`) — publish/read round-trip and fail-open behavior against a
fake Redis client (mirrors the injectable `redis_client_factory` pattern
already used by `app.services.v2.incremental.broker_observer`)."""
from app.services.runtime import answer_stream


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, ex=None):
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)


class RaisingRedis:
    def set(self, key, value, ex=None):
        raise ConnectionError("redis unavailable")

    def get(self, key):
        raise ConnectionError("redis unavailable")


def test_publish_then_read_round_trips_the_latest_text():
    fake = FakeRedis()
    answer_stream.publish_delta("turn-1", "你", redis_client_factory=lambda: fake)
    answer_stream.publish_delta("turn-1", "你好", redis_client_factory=lambda: fake)
    assert answer_stream.read_delta("turn-1", redis_client_factory=lambda: fake) == {"text": "你好"}


def test_read_before_any_publish_returns_none():
    fake = FakeRedis()
    assert answer_stream.read_delta("turn-never-started", redis_client_factory=lambda: fake) is None


def test_publish_is_fail_open_on_a_redis_outage():
    # must not raise — a Redis hiccup is never allowed to fail the Turn
    answer_stream.publish_delta("turn-1", "x", redis_client_factory=lambda: RaisingRedis())


def test_read_is_fail_open_on_a_redis_outage():
    assert answer_stream.read_delta("turn-1", redis_client_factory=lambda: RaisingRedis()) is None


def test_different_turns_are_isolated():
    fake = FakeRedis()
    answer_stream.publish_delta("turn-a", "A's answer", redis_client_factory=lambda: fake)
    answer_stream.publish_delta("turn-b", "B's answer", redis_client_factory=lambda: fake)
    assert answer_stream.read_delta("turn-a", redis_client_factory=lambda: fake) == {"text": "A's answer"}
    assert answer_stream.read_delta("turn-b", redis_client_factory=lambda: fake) == {"text": "B's answer"}
