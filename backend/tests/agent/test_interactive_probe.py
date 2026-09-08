"""Fixture-only `agent.interactive_probe` Celery task test (Task 6A).

Proves the probe is harmless and deterministic; it is never wired into
`app.tasks.celery_app`'s production include list (see topology test for the
queue-isolation contract that uses it via a fake broker).
"""
from __future__ import annotations

from app.tasks.v2.interactive_probe import interactive_probe


def test_interactive_probe_is_harmless_and_fixture_only():
    result = interactive_probe("probe-001")
    assert result == {"probe_id": "probe-001", "status": "ok"}
