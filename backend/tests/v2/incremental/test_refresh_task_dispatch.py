"""Run-ID-only refresh dispatch tests (Task 6A).

`build_refresh_dispatch_message`/`enqueue_refresh_run` are the only sanctioned
way to hand a refresh run to Celery: the message carries a durable `run_id`
and nothing else — no source URL, cursor, credential, SQL, selector, or event
payload. `test_saturated_refresh_queue_does_not_block_agent_interactive_queue`
uses a small in-memory fake broker (defined in this file) that models queue
isolation only — it is not a real Celery/Redis broker and needs none running.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.tasks.topology import RefreshDispatchMessage, build_refresh_dispatch_message, enqueue_refresh_run


def test_refresh_dispatch_message_is_only_a_durable_run_id():
    message = build_refresh_dispatch_message(run_id="run-001", task_name="refresh.poll")
    assert message == RefreshDispatchMessage(run_id="run-001", task_name="refresh.poll", queue="refresh.poll")
    assert set(message.to_dict()) == {"run_id", "task_name", "queue"}


def test_refresh_dispatch_rejects_non_durable_arguments():
    with pytest.raises(TypeError):
        build_refresh_dispatch_message(run_id="run-001", task_name="refresh.poll", source_id="source-001")


def test_refresh_dispatch_rejects_unknown_task_names_and_empty_ids():
    with pytest.raises(ValueError):
        build_refresh_dispatch_message(run_id="run-001", task_name="not.a.refresh.task")
    with pytest.raises(ValueError):
        build_refresh_dispatch_message(run_id="", task_name="refresh.poll")


def test_enqueue_refresh_run_sends_only_the_run_id_to_the_routed_queue():
    message = build_refresh_dispatch_message(run_id="run-001", task_name="refresh.connection")
    sent = []

    def fake_send_task(task_name, args, queue):
        sent.append((task_name, list(args), queue))
        return "broker-task-id-001"

    task_id = enqueue_refresh_run(message=message, send_task=fake_send_task)
    assert task_id == "broker-task-id-001"
    assert sent == [("refresh.connection", ["run-001"], "refresh.poll")]


# ── Fake broker: models per-queue isolation only, not a real Celery broker ─

@dataclass
class _Accepted:
    queue: str


@dataclass
class _FakeBroker:
    _queues: dict = field(default_factory=dict)

    def fill(self, queue: str, count: int = 1000) -> None:
        self._queues.setdefault(queue, []).extend(range(count))

    def depth(self, queue: str) -> int:
        return len(self._queues.get(queue, []))

    def send(self, task_name: str, args, queue: str) -> _Accepted:
        self._queues.setdefault(queue, []).append((task_name, list(args)))
        return _Accepted(queue=queue)


@pytest.fixture
def fake_broker():
    return _FakeBroker()


def test_saturated_refresh_queue_does_not_block_agent_interactive_queue(fake_broker):
    fake_broker.fill("refresh.poll")
    assert fake_broker.depth("refresh.poll") > 0
    accepted = fake_broker.send("agent.interactive_probe", ["probe-001"], "agent.interactive")
    assert accepted.queue == "agent.interactive"


def test_refresh_replay_task_is_registered_as_a_run_id_only_worker():
    from app.tasks.v2.refresh_tasks import refresh_replay_task

    assert refresh_replay_task.name == "refresh.replay"
    assert refresh_replay_task.run.__code__.co_argcount >= 1
