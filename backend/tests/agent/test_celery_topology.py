"""Celery execution topology tests (Task 6A).

Named queues/routes, bounded refresh worker settings, and the Compose
files' explicit queue-bound Python roles. No live broker or database is
required for anything in this file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.tasks.topology import (
    ALL_QUEUE_NAMES,
    TASK_ROUTES,
    get_refresh_task_options,
    load_refresh_worker_limits,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_refresh_routes_and_queues_are_explicit():
    assert set(ALL_QUEUE_NAMES) == {
        "refresh.schedule", "refresh.poll", "refresh.event", "refresh.replay",
        "artifact.extraction", "agent.interactive", "housekeeping",
    }
    assert TASK_ROUTES["refresh.poll"]["queue"] == "refresh.poll"
    assert TASK_ROUTES["refresh.event"]["queue"] == "refresh.event"
    assert TASK_ROUTES["refresh.replay"]["queue"] == "refresh.replay"
    assert TASK_ROUTES["agent.interactive_probe"]["queue"] == "agent.interactive"


def test_refresh_worker_limits_are_bounded_and_late_acknowledged():
    limits = load_refresh_worker_limits({})
    assert limits.prefetch_multiplier == 1
    assert limits.soft_time_limit_seconds < limits.hard_time_limit_seconds
    assert get_refresh_task_options("refresh.poll")["acks_late"] is True
    assert get_refresh_task_options("refresh.poll")["reject_on_worker_lost"] is True


def test_refresh_worker_limits_reject_non_positive_and_inverted_bounds():
    with pytest.raises(ValueError):
        load_refresh_worker_limits({"REFRESH_WORKER_CONCURRENCY": "0"})
    with pytest.raises(ValueError):
        load_refresh_worker_limits({"REFRESH_SOFT_TIME_LIMIT_SECONDS": "400", "REFRESH_HARD_TIME_LIMIT_SECONDS": "300"})


def test_get_refresh_task_options_rejects_non_refresh_names():
    with pytest.raises(ValueError):
        get_refresh_task_options("agent.turn_execute")


@pytest.mark.parametrize("compose_path", ["docker-compose.v2.yml", "docker-compose.agent.yml"])
def test_compose_uses_app_celery_and_queue_bound_roles(compose_path):
    text = (REPO_ROOT / compose_path).read_text()
    assert "-A app.tasks.celery_app" in text
    assert "-A app.tasks.extraction" not in text
    for queue in ALL_QUEUE_NAMES:
        assert queue in text
    assert "--prefetch-multiplier=1" in text
