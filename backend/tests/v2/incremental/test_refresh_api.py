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
from app.services.v2.incremental.operability import QueueObservation


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


def test_refresh_trigger_fails_closed_when_connection_config_cannot_be_decrypted(
    client, db, operator_headers,
):
    """`POST /sources/{source_id}/run` resolves its `resource` via
    `_source_resource`, which used to carry its own inline decrypt-or-
    plaintext read of `Connection.config` and silently fall back to
    `DEFAULT_RESOURCE` on any decrypt/JSON failure -- the same fail-open
    bug class fixed for `EventIngestService.accept()`, just reached via
    the manual refresh-trigger route instead of an inbound event. A
    connection with no pre-existing `RefreshSourceState` (so
    `_source_resource` must fall through to the connection config) and a
    corrupt `_encrypted` payload must reject the request instead of
    silently creating a `RefreshSourceState`/`RefreshRun` for a guessed
    resource."""
    db.add(Connection(
        id="source-002", name="broken-source", kind="rest", status="active",
        config={"_encrypted": "not-a-valid-ciphertext"},
        refresh_policy="micro_batch", cursor_contract="watermark_primary_key",
    ))
    db.commit()

    response = client.post(
        "/api/v2/refresh/sources/source-002/run",
        json={"mode": "micro_batch"}, headers=operator_headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "CONNECTION_CONFIG_UNAVAILABLE"
    assert db.query(RefreshSourceState).filter(RefreshSourceState.source_id == "source-002").count() == 0
    assert db.query(RefreshRun).filter(RefreshRun.source_id == "source-002").count() == 0


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


def test_refresh_health_uses_sanitized_broker_observations(
    client, db, refresh_source, operator_headers, monkeypatch,
):
    class FakeBrokerObserver:
        def observe(self, queues):
            assert tuple(queues) == (
                "refresh.schedule", "refresh.poll", "refresh.event", "refresh.replay",
            )
            return {
                queue: QueueObservation(
                    queue=queue,
                    depth=3,
                    oldest_queued_age_seconds=321.5,
                    worker_ready=queue != "refresh.event",
                )
                for queue in queues
            }

    monkeypatch.setattr(
        "app.routers.v2.refresh.RedisRefreshBrokerObserver", FakeBrokerObserver,
    )
    response = client.get("/api/v2/refresh/health", headers=operator_headers)

    assert response.status_code == 200
    body = response.json()
    assert all(item["depth"] == 3 for item in body["queues"])
    assert all(item["oldest_queued_age_seconds"] == 321.5 for item in body["queues"])
    assert body["readiness"]["broker_ready"] is True
    assert body["readiness"]["refresh_worker_ready"] is False
    assert "payload" not in response.text
    assert "credential" not in response.text


def test_refresh_health_reports_unavailable_broker_and_workers_truthfully(
    client, db, refresh_source, operator_headers, monkeypatch,
):
    class UnavailableBrokerObserver:
        def observe(self, queues):
            return {
                queue: QueueObservation(
                    queue=queue, depth=-1,
                    oldest_queued_age_seconds=None, worker_ready=False,
                )
                for queue in queues
            }

    monkeypatch.setattr(
        "app.routers.v2.refresh.RedisRefreshBrokerObserver", UnavailableBrokerObserver,
    )
    response = client.get("/api/v2/refresh/health", headers=operator_headers)

    assert response.status_code == 200
    body = response.json()
    assert all(item["depth"] == -1 for item in body["queues"])
    assert all(item["oldest_queued_age_seconds"] is None for item in body["queues"])
    assert body["readiness"]["broker_ready"] is False
    assert body["readiness"]["refresh_worker_ready"] is False


def test_refresh_trigger_rejects_mode_incompatible_with_persisted_event_policy(
    client, db, refresh_source, operator_headers, monkeypatch,
):
    source = db.get(Connection, "source-001")
    state = db.get(RefreshSourceState, "state-001")
    source.refresh_policy = "event_driven"
    state.configuration = {"refresh_policy": "event_driven"}
    db.commit()

    monkeypatch.setattr(
        "app.routers.v2.refresh.enqueue_refresh_run",
        lambda **_: pytest.fail("incompatible event policy must not be dispatched to refresh.poll"),
    )
    response = client.post(
        "/api/v2/refresh/sources/source-001/run",
        json={"mode": "batch"}, headers=operator_headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "REFRESH_POLICY_MISMATCH"
    assert db.query(RefreshRun).count() == 0


def test_legacy_connection_sync_rejects_persisted_event_policy(
    client, db, refresh_source, operator_headers, monkeypatch,
):
    from app.main import app
    from app.routers.v2 import connections as connections_router

    source = db.get(Connection, "source-001")
    state = db.get(RefreshSourceState, "state-001")
    source.refresh_policy = "event_driven"
    state.configuration = {"refresh_policy": "event_driven"}
    db.commit()

    def override_connections_db():
        yield db

    app.dependency_overrides[connections_router.get_db] = override_connections_db
    dispatched = []
    monkeypatch.setattr(
        "app.tasks.v2.refresh_tasks.refresh_connection_task.delay",
        lambda run_id: dispatched.append(run_id),
    )
    try:
        response = client.post(
            "/api/v2/connections/source-001/sync", headers=operator_headers,
        )
    finally:
        app.dependency_overrides.pop(connections_router.get_db, None)

    assert response.status_code == 422
    assert response.json()["detail"] == "EVENT_TRIGGER_REQUIRES_SIGNATURE"
    assert dispatched == []
    assert db.query(RefreshRun).count() == 0


def test_legacy_connection_sync_keeps_valid_batch_compatibility(
    client, db, refresh_source, operator_headers, monkeypatch,
):
    from app.main import app
    from app.routers.v2 import connections as connections_router

    source = db.get(Connection, "source-001")
    state = db.get(RefreshSourceState, "state-001")
    source.refresh_policy = "batch"
    state.configuration = {"refresh_policy": "batch"}
    db.commit()

    def override_connections_db():
        yield db

    dispatched = []
    app.dependency_overrides[connections_router.get_db] = override_connections_db
    monkeypatch.setattr(
        "app.tasks.v2.refresh_tasks.refresh_connection_task.delay",
        lambda run_id: dispatched.append(run_id),
    )
    try:
        response = client.post(
            "/api/v2/connections/source-001/sync", headers=operator_headers,
        )
    finally:
        app.dependency_overrides.pop(connections_router.get_db, None)

    assert response.status_code == 200
    assert response.json()["status"] == "sync_triggered"
    assert dispatched == [response.json()["run_id"]]
    persisted = db.get(RefreshRun, response.json()["run_id"])
    assert persisted.policy == "batch"
    assert persisted.dispatch_queue == "refresh.poll"


def test_saturated_refresh_queue_does_not_block_status_or_agent_dispatch(
    client, db, refresh_source, operator_headers, monkeypatch,
):
    class SaturatedBrokerObserver:
        def observe(self, queues):
            return {
                queue: QueueObservation(
                    queue=queue, depth=100,
                    oldest_queued_age_seconds=900.0, worker_ready=True,
                )
                for queue in queues
            }

    monkeypatch.setattr(
        "app.routers.v2.refresh.RedisRefreshBrokerObserver", SaturatedBrokerObserver,
    )
    health = client.get("/api/v2/refresh/health", headers=operator_headers)
    assert health.status_code == 200
    assert all(item["depth"] == 100 for item in health.json()["queues"])

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


def test_refresh_schedule_api_reads_persisted_timezone_and_calendar_after_reload(
    client, db, refresh_source, operator_headers,
):
    client.put(
        "/api/v2/refresh/sources/source-001/schedule",
        json={"cron_expr": "0 2 * * *", "timezone": "Asia/Shanghai", "business_calendar": ["2026-10-01"]},
        headers=operator_headers,
    )
    response = client.get("/api/v2/refresh/sources/source-001/schedule", headers=operator_headers)
    assert response.status_code == 200
    assert response.json()["timezone"] == "Asia/Shanghai"
    assert response.json()["business_calendar"] == ["2026-10-01"]


def test_refresh_schedule_read_uses_source_schedule_when_pipeline_has_same_id(
    client, db, refresh_source, operator_headers,
):
    from app.models.v2.refresh import RefreshSchedule

    db.add(RefreshSchedule(
        id="pipeline-schedule-same-id", target_type="pipeline", target_id="source-001",
        cron_expression="0 3 * * *", timezone="UTC", excluded_dates=["PIPELINE"],
        enabled=True, max_pending_runs=1,
    ))
    db.commit()
    client.put(
        "/api/v2/refresh/sources/source-001/schedule",
        json={"cron_expr": "0 2 * * *", "timezone": "Asia/Shanghai", "business_calendar": ["SOURCE"]},
        headers=operator_headers,
    )

    response = client.get("/api/v2/refresh/sources/source-001/schedule", headers=operator_headers)
    assert response.status_code == 200
    assert response.json()["timezone"] == "Asia/Shanghai"
    assert response.json()["business_calendar"] == ["SOURCE"]


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
