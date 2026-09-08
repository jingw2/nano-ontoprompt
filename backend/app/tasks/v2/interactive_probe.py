"""Fixture-only diagnostic Celery task (Task 6A).

`agent.interactive_probe` exists solely to prove — via this task's own
fake-broker test — that a saturated refresh queue does not block the
`agent.interactive` queue. It touches no Agent state, connector, database,
or production payload, and is deliberately NOT added to
`app.tasks.celery_app`'s `include` list, so no production worker ever
registers or executes it; it is denylisted for production dispatch by
being unreachable rather than by a runtime check.
"""
from __future__ import annotations

from typing import Mapping

from app.tasks.celery_app import celery_app


@celery_app.task(name="agent.interactive_probe")
def interactive_probe(probe_id: str) -> Mapping[str, str]:
    return {"probe_id": probe_id, "status": "ok"}
