"""Task 9 managed webhook/outbox event-ingestion contract tests."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models.v2.connection import Connection
from app.models.v2.dataset import DatasetVersion
from app.models.v2.pipeline import PipelineRunInput
from app.models.v2.refresh import RefreshInboxEvent, RefreshRun, RefreshSourceState
from app.schemas.refresh import ChangeEnvelope, RefreshLeaseError
from app.services.v2.incremental.event_adapters import (
    EventIngressError,
    ManagedOutboxAdapter,
    ManagedWebhookAdapter,
)
from app.services.v2.incremental.event_ingest import (
    EventIngestService,
    replay_dead_letter,
)
from app.tasks.topology import build_refresh_dispatch_message


FIXED_NOW = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc)
SECRET = "event-test-secret"


def _record(*, event_id: str = "evt-001", watermark: str = "2026-08-26T01:00:00Z") -> dict:
    return {
        "version": 1,
        "event_id": event_id,
        "source_id": "source-001",
        "resource": "orders",
        "operation": "upsert",
        "primary_key": "100",
        "payload": {"id": "100", "state": "ready"},
        "watermark": watermark,
        "source_cursor": None,
        "schema_hash": "schema-v1",
        "occurred_at": FIXED_NOW.isoformat(),
    }


def _body(**overrides) -> bytes:
    record = _record(**overrides)
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _signature(body: bytes, *, timestamp: str = "2026-08-26T00:00:00+00:00") -> str:
    digest = hmac.new(SECRET.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _source(db, *, schema_hash: str = "schema-v1") -> None:
    db.add(Connection(
        id="source-001", name="orders-source", kind="rest",
        config={"cursor_contract": "watermark_primary_key"}, status="active",
    ))
    db.add(RefreshSourceState(
        id="state-001", source_id="source-001", resource="orders",
        cursor_contract="watermark_primary_key", config_version=1,
        configuration={"schema_hash": schema_hash}, fencing_token=0,
    ))
    db.commit()


def test_webhook_signature_is_checked_before_payload_processing():
    with pytest.raises(EventIngressError) as exc:
        ManagedWebhookAdapter().verify_and_normalize(
            b"not-json", source_id="source-001", signature="wrong",
            timestamp=FIXED_NOW.isoformat(), secret_ref=SECRET, now=FIXED_NOW,
        )
    assert exc.value.reason_code == "INVALID_SIGNATURE"


def test_webhook_adapter_normalizes_a_signed_versioned_envelope():
    body = _body()
    envelope = ManagedWebhookAdapter().verify_and_normalize(
        body, source_id="source-001", signature=_signature(body),
        timestamp=FIXED_NOW.isoformat(), secret_ref=SECRET, now=FIXED_NOW,
    )
    assert isinstance(envelope, ChangeEnvelope)
    assert envelope.event_id == "evt-001"
    assert envelope.schema_hash == "schema-v1"
    assert envelope.occurred_at == FIXED_NOW


def test_event_ingest_rejects_expired_webhook_signature():
    old = FIXED_NOW - timedelta(minutes=10)
    body = _body()
    with pytest.raises(EventIngressError) as exc:
        ManagedWebhookAdapter(max_age_seconds=300).verify_and_normalize(
            body, source_id="source-001", signature=_signature(body, timestamp=old.isoformat()),
            timestamp=old.isoformat(), secret_ref=SECRET, now=FIXED_NOW,
        )
    assert exc.value.reason_code == "EXPIRED_SIGNATURE"


def test_webhook_accepts_unix_timestamp_headers():
    body = _body()
    timestamp = str(int(FIXED_NOW.timestamp()))
    digest = hmac.new(SECRET.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()
    envelope = ManagedWebhookAdapter().verify_and_normalize(
        body, source_id="source-001", signature=f"sha256={digest}",
        timestamp=timestamp, secret_ref=SECRET, now=FIXED_NOW,
    )
    assert envelope.event_id == "evt-001"


def test_event_ingest_rejects_replayed_event_id():
    adapter = ManagedWebhookAdapter()
    first_body = _body()
    adapter.verify_and_normalize(
        first_body, source_id="source-001", signature=_signature(first_body),
        timestamp=FIXED_NOW.isoformat(), secret_ref=SECRET, now=FIXED_NOW,
    )
    replayed = _body()
    replayed = replayed.replace(b"ready", b"tampered")
    with pytest.raises(EventIngressError) as exc:
        adapter.verify_and_normalize(
            replayed, source_id="source-001", signature=_signature(replayed),
            timestamp=FIXED_NOW.isoformat(), secret_ref=SECRET, now=FIXED_NOW,
        )
    assert exc.value.reason_code == "REPLAY_DETECTED"


def test_event_ingest_rejects_schema_drift(db):
    _source(db, schema_hash="schema-v2")
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    with pytest.raises(EventIngressError) as exc:
        service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    assert exc.value.reason_code == "SCHEMA_DRIFT"


def test_outbox_adapter_normalizes_without_broker_dependency():
    envelope = ManagedOutboxAdapter.normalize(
        _record(), source_id="source-001", received_at=FIXED_NOW,
    )
    assert envelope.event_id == "evt-001"


def test_outbox_adapter_rejects_source_resource_mismatch():
    record = _record()
    record["source_id"] = "source-999"
    with pytest.raises(EventIngressError) as exc:
        ManagedOutboxAdapter.normalize(record, source_id="source-001", received_at=FIXED_NOW)
    assert exc.value.reason_code == "INVALID_CHANGE_ENVELOPE"


def test_outbox_adapter_infers_opaque_contract_from_source_cursor():
    record = _record()
    record.pop("watermark")
    record["source_cursor"] = "opaque-001"
    record.pop("primary_key")

    envelope = ManagedOutboxAdapter.normalize(record, source_id="source-001", received_at=FIXED_NOW)

    assert envelope.source_cursor == "opaque-001"


def test_event_accept_is_durable_and_equal_event_is_a_duplicate(db):
    _source(db)
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)

    first = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    duplicate = service.accept(db, envelope, lease_owner="event-worker-002", now=FIXED_NOW)

    assert first.status == "received"
    assert duplicate.status == "duplicate"
    assert duplicate.run_id == first.run_id
    assert db.query(RefreshInboxEvent).count() == 1
    assert db.query(RefreshRun).count() == 1


def test_event_accept_recovers_when_claim_fails_after_inbox_commit(db, monkeypatch):
    _source(db)
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    from app.services.v2.incremental import event_ingest

    original_claim = event_ingest.claim_refresh_run
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RefreshLeaseError("REFRESH_LEASE_ACTIVE", "fixture lease failure")
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(event_ingest, "claim_refresh_run", fail_once)
    with pytest.raises(RefreshLeaseError):
        service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)

    durable = db.query(RefreshInboxEvent).one()
    assert durable.state == "received"
    assert durable.run_id is None

    recovered = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    assert recovered.status == "received"
    assert recovered.run_id is not None
    assert db.query(RefreshRun).count() == 1


def test_event_accept_uses_the_connection_cursor_contract_when_provisioning_state(db):
    db.add(Connection(
        id="source-opaque", name="opaque-source", kind="rest", status="active",
        cursor_contract="opaque_source_cursor", config={},
    ))
    db.commit()
    envelope = ManagedOutboxAdapter.normalize(
        {
            "version": 1, "event_id": "evt-opaque", "source_id": "source-opaque",
            "resource": "orders", "operation": "upsert", "primary_key": "100",
            "payload": {"id": "100"}, "watermark": "2026-08-26T01:00:00Z",
            "schema_hash": "schema-v1", "occurred_at": FIXED_NOW.isoformat(),
        }, source_id="source-opaque", received_at=FIXED_NOW,
    )
    with pytest.raises(EventIngressError) as exc:
        EventIngestService().accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    assert exc.value.reason_code == "SCHEMA_DRIFT"
    assert db.query(RefreshSourceState).filter(RefreshSourceState.source_id == "source-opaque").count() == 0


def test_event_worker_materializes_lineage_and_advances_cursor(db):
    _source(db)
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)

    result = service.process(db, run_id=receipt.run_id, lease_owner="event-worker-001", now=FIXED_NOW)

    assert result.status == "succeeded"
    assert result.cursor_after.watermark == "2026-08-26T01:00:00Z"
    assert result.input_dataset_version_ids
    assert db.query(DatasetVersion).count() == 1
    assert db.query(PipelineRunInput).count() == 1
    inbox = db.query(RefreshInboxEvent).one()
    assert inbox.state == "processed"


def test_duplicate_after_processing_reports_processed_and_current_cursor(db):
    _source(db)
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    first = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    service.process(db, run_id=first.run_id, lease_owner="event-worker-001", now=FIXED_NOW)

    duplicate = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)

    assert duplicate.status == "processed"
    assert duplicate.cursor is not None
    assert duplicate.cursor.watermark == "2026-08-26T01:00:00Z"


def test_event_worker_cancellation_leaves_inbox_and_cursor_unchanged(db):
    _source(db)
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    service.request_cancellation(db, run_id=receipt.run_id, requested_by="operator-001", reason="stop", now=FIXED_NOW)

    result = service.process(db, run_id=receipt.run_id, lease_owner="event-worker-001", now=FIXED_NOW)

    assert result.status == "cancelled"
    assert result.cursor_after == result.cursor_before
    assert result.input_dataset_version_ids == []
    assert db.query(DatasetVersion).count() == 0
    assert db.query(PipelineRunInput).count() == 0
    assert db.query(RefreshInboxEvent).one().state == "received"


def test_refresh_event_worker_message_contains_only_durable_run_id():
    message = build_refresh_dispatch_message(run_id="run-event-001", task_name="refresh.event")
    assert message.to_dict() == {
        "run_id": "run-event-001", "task_name": "refresh.event", "queue": "refresh.event",
    }


def test_webhook_route_returns_only_event_run_and_status(client, db, monkeypatch):
    _source(db)
    db.get(Connection, "source-001").config = {
        "cursor_contract": "watermark_primary_key", "webhook_secret_ref": SECRET,
    }
    db.commit()
    from app.tasks.v2 import refresh_tasks

    monkeypatch.setattr(refresh_tasks.refresh_event_task, "delay", lambda run_id: None)
    body = _body()
    timestamp = datetime.now(timezone.utc).isoformat()
    response = client.post(
        "/api/v2/refresh/events/webhook/source-001",
        content=body,
        headers={"X-Webhook-Signature": _signature(body, timestamp=timestamp), "X-Webhook-Timestamp": timestamp},
    )

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"event_id", "run_id", "status"}
    assert response.json()["event_id"] == "evt-001"
    assert response.json()["status"] == "received"


def test_dlq_replay_creates_new_run_and_retains_original(db):
    _source(db)
    service = EventIngestService(max_attempts=1)
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    service.dead_letter(db, run_id=receipt.run_id, reason="fixture failure", now=FIXED_NOW)
    dead_letter_id = db.query(service.dead_letter_model).one().id

    replay = replay_dead_letter(
        db, dead_letter_id=dead_letter_id, operator_id="operator-001", now=FIXED_NOW,
    )

    assert replay.id != receipt.run_id
    assert db.get(service.dead_letter_model, dead_letter_id).replay_status == "replayed"
