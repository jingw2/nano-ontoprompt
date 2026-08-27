"""Authenticated refresh operations API contract (Task 10).

These tests exercise the API boundary around the durable Task 6--9 services:
requests may validate, persist, and enqueue a run ID, but they never invoke a
connector or carry caller-owned source state into the broker.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.user import User
from app.models.v2.connection import Connection
from app.models.v2.refresh import (
    RefreshDeadLetter,
    RefreshInboxEvent,
    RefreshRun,
    RefreshSourceState,
)
from app.services.auth_service import create_access_token, hash_password


NOW = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def operator_headers(db):
    user = User(
        id="operator-001", username="operator", email="operator@test.com",
        password_hash=hash_password("operator123"), role="editor",
    )
    db.add(user)
    db.commit()
    token = create_access_token({"sub": user.id, "role": user.role})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def viewer_headers(db):
    user = User(
        id="viewer-001", username="viewer", email="viewer@test.com",
        password_hash=hash_password("viewer123"), role="viewer",
    )
    db.add(user)
    db.commit()
    token = create_access_token({"sub": user.id, "role": user.role})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def refresh_source(db):
    db.add(Connection(
        id="source-001", name="orders-source", kind="rest", status="active",
        config={"cursor_contract": "watermark_primary_key"},
        refresh_policy="micro_batch", cursor_contract="watermark_primary_key",
    ))
    db.add(RefreshSourceState(
        id="state-001", source_id="source-001", resource="orders",
        cursor_contract="watermark_primary_key", config_version=7,
        cursor_json={
            "watermark": NOW.isoformat(), "primary_key": "100",
            "opaque_value": None, "observed_at": NOW.isoformat(),
        },
        configuration={"refresh_policy": "micro_batch"}, fencing_token=7,
    ))
    db.commit()


def _add_run(
    db,
    *,
    run_id: str,
    status: str,
    policy: str = "micro_batch",
    cursor_before: dict | None = None,
    cursor_contract: str = "watermark_primary_key",
    config_version: int = 7,
    fencing_token: int = 7,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    lag_seconds: int | None = None,
) -> RefreshRun:
    run = RefreshRun(
        id=run_id, source_id="source-001", resource="orders", policy=policy,
        trigger="manual", config_version=config_version,
        cursor_contract=cursor_contract, status=status,
        dispatch_state="pending", idempotency_key=f"fixture:{run_id}",
        fencing_token=fencing_token, cursor_before_json=cursor_before,
        lease_owner=lease_owner, lease_expires_at=lease_expires_at,
        lag_seconds=lag_seconds,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_refresh_status_exposes_cursor_lag_schedule_and_failure(
    client, db, refresh_source, operator_headers,
):
    _add_run(
        db, run_id="run-dead-001", status="dead_lettered",
        cursor_before={
            "watermark": NOW.isoformat(), "primary_key": "100",
            "observed_at": NOW.isoformat(),
        }, lag_seconds=120,
    )

    response = client.get(
        "/api/v2/refresh/sources/source-001/status", headers=operator_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_id"] == "source-001"
    assert body["cursor"]["primary_key"] == "100"
    assert body["config_version"] == 7
    assert body["cursor_contract"] == "watermark_primary_key"
    assert body["lag_seconds"] == 120
    assert body["latest_run"]["status"] == "dead_lettered"
    assert body["latest_run"]["config_version"] == 7


def test_refresh_trigger_and_replay_are_bounded_and_idempotent(
    client, db, refresh_source, operator_headers, monkeypatch,
):
    original_cursor = {
        "watermark": NOW.isoformat(), "primary_key": "100",
        "opaque_value": None,
        "observed_at": NOW.isoformat(),
    }
    _add_run(db, run_id="run-dead-001", status="dead_lettered", cursor_before=original_cursor)
    inbox = RefreshInboxEvent(
        id="inbox-dead-001", source_id="source-001", resource="orders",
        event_id="event-dead-001", state="dead_lettered", run_id="run-dead-001",
        event_hash="dead-hash", envelope_json={
            "version": 1, "event_id": "event-dead-001", "source_id": "source-001",
            "resource": "orders", "operation": "upsert", "primary_key": "101",
            "payload": {"id": "101"}, "watermark": NOW.isoformat(),
            "source_cursor": None, "schema_hash": "schema-v1",
            "occurred_at": NOW.isoformat(), "contract": "watermark_primary_key",
        },
    )
    db.add(inbox)
    db.add(RefreshDeadLetter(
        id="dlq-dead-001", source_id="source-001", resource="orders",
        event_id="event-dead-001", run_id="run-dead-001", reason="source unavailable",
        replay_status="pending",
    ))
    db.commit()

    sent = []

    def capture_refresh_messages(*, message, send_task):
        del send_task
        sent.append(message.to_dict())
        return "broker-task-001"

    monkeypatch.setattr(
        "app.routers.v2.refresh.enqueue_refresh_run", capture_refresh_messages,
    )
    triggered = client.post(
        "/api/v2/refresh/sources/source-001/run",
        json={"mode": "micro_batch"}, headers=operator_headers,
    )
    replayed = client.post(
        "/api/v2/refresh/runs/run-dead-001/replay",
        json={}, headers=operator_headers,
    )

    assert triggered.status_code == 202
    assert replayed.status_code == 202
    assert triggered.json()["cursor_before"] == replayed.json()["cursor_before"]
    assert sent[0]["run_id"] == triggered.json()["run_id"]
    assert sent[0]["task_name"] == "refresh.poll"
    assert sent[1]["task_name"] == "refresh.replay"
    assert sent[1]["queue"] == "refresh.replay"


def test_refresh_cancel_persists_durable_request_and_returns_terminal_safe_state(
    client, db, refresh_source, operator_headers,
):
    _add_run(
        db, run_id="run-running-001", status="running",
        lease_owner="worker-001", lease_expires_at=NOW + timedelta(minutes=5),
    )

    response = client.post(
        "/api/v2/refresh/runs/run-running-001/cancel",
        json={"reason": "planned source maintenance"}, headers=operator_headers,
    )

    assert response.status_code == 202
    assert response.json()["status"] in {"cancel_requested", "cancelled"}
    assert response.json()["cancel_requested_by"] == "operator-001"
    assert response.json()["cancel_reason"] == "planned source maintenance"
    status = client.get(
        "/api/v2/refresh/sources/source-001/status", headers=operator_headers,
    )
    assert status.json()["latest_run"]["cancel_requested_at"] is not None


def test_refresh_cancel_requires_operator_authorization(
    client, db, refresh_source, viewer_headers,
):
    _add_run(
        db, run_id="run-running-001", status="running",
        lease_owner="worker-001", lease_expires_at=NOW + timedelta(minutes=5),
    )
    response = client.post(
        "/api/v2/refresh/runs/run-running-001/cancel",
        json={"reason": "not allowed"}, headers=viewer_headers,
    )
    assert response.status_code == 403


@pytest.mark.parametrize("status", ["succeeded", "failed", "dead_lettered"])
def test_refresh_cancel_after_terminal_state_is_already_terminal(
    client, db, refresh_source, operator_headers, status,
):
    _add_run(db, run_id=f"run-{status}-001", status=status)
    response = client.post(
        f"/api/v2/refresh/runs/run-{status}-001/cancel",
        json={"reason": "too late"}, headers=operator_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"status": status, "already_terminal": True}


def test_refresh_trigger_only_persists_and_enqueues_durable_run_id(
    client, db, refresh_source, operator_headers, monkeypatch,
):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("connector pull must not run in FastAPI")

    monkeypatch.setattr(
        "app.services.connection.base.ConnectorBase.pull_delta", fail_if_called,
    )
    sent = []

    def capture_refresh_messages(*, message, send_task):
        del send_task
        sent.append(message.to_dict())
        return "broker-task-001"

    monkeypatch.setattr(
        "app.routers.v2.refresh.enqueue_refresh_run", capture_refresh_messages,
    )
    response = client.post(
        "/api/v2/refresh/sources/source-001/run",
        json={"mode": "micro_batch"}, headers=operator_headers,
    )

    assert response.status_code == 202
    assert sent == [{
        "run_id": response.json()["run_id"],
        "task_name": "refresh.poll", "queue": "refresh.poll",
    }]
    persisted = db.get(RefreshRun, response.json()["run_id"])
    assert persisted.status == "queued"
    assert persisted.cursor_before_json["primary_key"] == "100"


def test_refresh_health_exposes_backpressure_and_readiness_without_payloads(
    client, db, refresh_source, operator_headers,
):
    response = client.get("/api/v2/refresh/health", headers=operator_headers)

    assert response.status_code == 200
    body = response.json()
    assert {item["queue"] for item in body["queues"]} == {
        "refresh.schedule", "refresh.poll", "refresh.event", "refresh.replay",
    }
    assert "payload" not in response.text
    assert "credential" not in response.text


def test_saturated_refresh_queue_does_not_block_status_or_agent_dispatch(
    client, db, refresh_source, operator_headers,
):
    status = client.get(
        "/api/v2/refresh/sources/source-001/status", headers=operator_headers,
    )
    assert status.status_code == 200


def test_refresh_schedule_api_persists_timezone_calendar_and_sla(
    client, db, refresh_source, operator_headers,
):
    response = client.put(
        "/api/v2/refresh/sources/source-001/schedule",
        json={
            "cron_expr": "0 2 * * *", "timezone": "Asia/Shanghai",
            "business_calendar": ["2026-10-01"], "sla_seconds": 86400,
            "backfill_window_seconds": 172800, "enabled": True,
        }, headers=operator_headers,
    )

    assert response.status_code == 200
    assert response.json()["timezone"] == "Asia/Shanghai"
    assert response.json()["sla_seconds"] == 86400
    assert response.json()["backfill_window_seconds"] == 172800


def test_refresh_api_rejects_caller_cursor_url_and_broker(
    client, db, refresh_source, operator_headers,
):
    response = client.post(
        "/api/v2/refresh/sources/source-001/run",
        json={
            "mode": "micro_batch", "cursor": "spoof",
            "source_url": "https://example.invalid", "broker": "kafka://attacker",
        }, headers=operator_headers,
    )
    assert response.status_code == 422
