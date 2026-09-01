"""Task 9 managed webhook/outbox event-ingestion contract tests."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.v2.connection import Connection
from app.models.v2.dataset import DatasetVersion
from app.models.v2.pipeline import PipelineRun, PipelineRunInput
from app.models.v2.refresh import RefreshDeadLetter, RefreshInboxEvent, RefreshRun, RefreshSourceState
from app.schemas.refresh import ChangeEnvelope, ConfigurationDriftError, RefreshLeaseError
from app.services.v2.incremental import event_ingest
from app.services.v2.incremental.contract import request_refresh_cancellation, update_source_configuration
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

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


class _ConcurrentEventDB:
    """A real Session (used directly like `db`) that also exposes an
    independent `.new_session()` for a genuine two-connection race, mirroring
    `test_refresh_contract.py`'s `concurrent_refresh_db` fixture. Kept local
    to this file rather than shared/imported so this Task 9 fix does not
    touch Task 6/8 test infrastructure."""

    def __init__(self, engine):
        self._engine = engine
        self.session = sessionmaker(bind=engine)()

    def new_session(self):
        return sessionmaker(bind=self._engine)()

    def __getattr__(self, name):
        return getattr(self.session, name)


@pytest.fixture
def concurrent_event_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "event_" + uuid.uuid4().hex
    admin_engine = create_engine(TEST_DATABASE_URL)
    try:
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    finally:
        admin_engine.dispose()

    import subprocess
    import sys
    from pathlib import Path
    from urllib.parse import quote

    backend_dir = Path(__file__).resolve().parents[3]
    scoped_url = f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"
    result = subprocess.run(
        [sys.executable, "scripts/run_migrations.py", "upgrade", "head"],
        cwd=backend_dir, env=dict(os.environ, DATABASE_URL=scoped_url),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"migration failed:\n{result.stdout}\n{result.stderr}"

    engine = create_engine(scoped_url)
    wrapper = _ConcurrentEventDB(engine)
    try:
        yield wrapper
    finally:
        wrapper.session.close()
        engine.dispose()
        cleanup_engine = create_engine(TEST_DATABASE_URL)
        try:
            with cleanup_engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            cleanup_engine.dispose()


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


def test_webhook_adapter_has_no_in_memory_replay_state():
    """The router instantiates a fresh `ManagedWebhookAdapter()` per request
    (see `refresh_events.py`), so any in-memory replay cache on the adapter
    would never persist across requests and would be misleading, dead
    weight. Real replay protection is the durable inbox comparison in
    `EventIngestService.accept()` (see the test below), not an ephemeral
    per-instance cache."""
    assert not hasattr(ManagedWebhookAdapter(), "_seen_event_hashes")


def test_event_ingest_rejects_replayed_event_id_via_durable_inbox(db):
    """Two deliveries of the same `event_id` with different payloads are
    rejected as REPLAY_DETECTED by the durable inbox in
    `EventIngestService.accept()`, using two independent adapter instances
    (as the router does per-request) to prove this protection does not rely
    on adapter-instance reuse."""
    _source(db)
    first_body = _body()
    first_envelope = ManagedWebhookAdapter().verify_and_normalize(
        first_body, source_id="source-001", signature=_signature(first_body),
        timestamp=FIXED_NOW.isoformat(), secret_ref=SECRET, now=FIXED_NOW,
    )
    EventIngestService().accept(db, first_envelope, lease_owner="event-worker-001", now=FIXED_NOW)

    replayed = _body().replace(b"ready", b"tampered")
    replayed_envelope = ManagedWebhookAdapter().verify_and_normalize(
        replayed, source_id="source-001", signature=_signature(replayed),
        timestamp=FIXED_NOW.isoformat(), secret_ref=SECRET, now=FIXED_NOW,
    )
    with pytest.raises(EventIngressError) as exc:
        EventIngestService().accept(db, replayed_envelope, lease_owner="event-worker-002", now=FIXED_NOW)
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
    first = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    assert first.status == "received"
    assert first.run_id is None

    durable = db.query(RefreshInboxEvent).one()
    assert durable.state == "received"
    assert durable.run_id is None

    duplicate = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    assert duplicate.status == "duplicate"
    recovered = service.drain_pending(
        db, dispatch=lambda _run_id: None, lease_owner="event-worker-001", now=FIXED_NOW,
    )[0]
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


def test_event_accept_rejects_mismatched_contract_when_connection_config_is_encrypted(db):
    """`Connection.config` is normally encrypted (see
    `test_webhook_route_decrypts_managed_connection_config_for_secret_ref`'s
    docstring for the exact real shape `POST /api/v2/connections` persists),
    and the dedicated `Connection.cursor_contract` column stays NULL until an
    operator explicitly calls `PUT .../refresh-configuration`. A raw
    `dict(connection.config or {})` read in `accept()` would see only
    `{"_encrypted": ...}` and silently fall back to trusting whatever cursor
    contract/schema hash the FIRST incoming event envelope itself claims,
    letting that envelope define the durable `RefreshSourceState` instead of
    the operator's real configuration. This mirrors
    `test_event_ingest_rejects_schema_drift`'s plaintext-config coverage but
    against the real encrypted shape."""
    from app.services import encryption_service

    db.add(Connection(
        id="source-001", name="orders-source", kind="rest", status="active",
        config={"_encrypted": encryption_service.encrypt(json.dumps({
            "cursor_contract": "watermark_primary_key", "schema_hash": "the-real-schema",
        }))},
    ))
    db.commit()

    record = _record(event_id="evt-first")
    record.pop("watermark")
    record.pop("primary_key")
    record["source_cursor"] = "opaque-001"
    record["schema_hash"] = "a-different-schema"
    envelope = ManagedOutboxAdapter.normalize(record, source_id="source-001", received_at=FIXED_NOW)

    with pytest.raises(EventIngressError) as exc:
        EventIngestService().accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    assert exc.value.reason_code == "SCHEMA_DRIFT"
    assert db.query(RefreshSourceState).filter(RefreshSourceState.source_id == "source-001").count() == 0


def test_event_accept_fails_closed_when_connection_config_cannot_be_decrypted(db):
    """A connection whose `_encrypted` config cannot be decrypted (corrupt
    ciphertext, key rotation, storage bit-rot) must never be treated the
    same as "no configuration set" -- that would silently let
    `configured_contract` fall through to whatever the incoming envelope
    itself claims, defining the durable baseline from untrusted input.
    `accept()` must reject the event instead, and must not create a
    `RefreshSourceState` or `RefreshInboxEvent` row for it."""
    db.add(Connection(
        id="source-001", name="orders-source", kind="rest", status="active",
        config={"_encrypted": "not-a-valid-ciphertext"},
    ))
    db.commit()

    record = _record(event_id="evt-first")
    record.pop("watermark")
    record.pop("primary_key")
    record["source_cursor"] = "opaque-001"
    record["schema_hash"] = "a-different-schema"
    envelope = ManagedOutboxAdapter.normalize(record, source_id="source-001", received_at=FIXED_NOW)

    with pytest.raises(EventIngressError) as exc:
        EventIngestService().accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    assert exc.value.reason_code == "CONFIGURATION_UNAVAILABLE"
    assert db.query(RefreshSourceState).filter(RefreshSourceState.source_id == "source-001").count() == 0
    assert db.query(RefreshInboxEvent).filter(RefreshInboxEvent.source_id == "source-001").count() == 0


def test_dispatch_pending_yields_to_a_committed_cancellation(db):
    """An operator's committed `cancel_requested` must win over publishing a
    stale event to the broker. `drain_pending` loads the run via
    `db.get(RefreshRun, row.run_id)` with no intervening commit/rollback
    before calling `dispatch_pending`, which then re-fetches the same row
    FOR UPDATE -- so a cancellation committed by another session in that
    window must still be observed. Before the fix, `dispatch_pending`'s
    terminal guard set did not include `cancel_requested`, so this call
    would fall through and publish the event to the broker anyway."""
    _source(db)
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(event_id="evt-cancel-dispatch"), source_id="source-001", received_at=FIXED_NOW)
    receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    assert receipt.run_id is not None
    assert db.get(RefreshRun, receipt.run_id).status == "running"

    cancelled = request_refresh_cancellation(
        db, run_id=receipt.run_id, requested_by="operator-001", reason="stop", now=FIXED_NOW,
    )
    assert cancelled.status == "cancel_requested"

    dispatched: list[str] = []
    result = service.dispatch_pending(
        db, run_id=receipt.run_id, dispatch=lambda run_id: dispatched.append(run_id), now=FIXED_NOW,
    )

    assert result.status == "cancelled"
    assert dispatched == []
    assert db.get(RefreshRun, receipt.run_id).status == "cancelled"


def test_broker_failure_is_reconciled_from_the_durable_inbox(db):
    _source(db)
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)

    failed = service.dispatch_pending(
        db, run_id=receipt.run_id, dispatch=lambda _run_id: (_ for _ in ()).throw(RuntimeError("broker down")),
        now=FIXED_NOW,
    )

    assert failed.status == "publish_failed"
    assert db.query(RefreshInboxEvent).one().state == "received"
    assert db.get(RefreshRun, receipt.run_id).dispatch_state == "publish_failed"

    dispatched: list[str] = []
    recovered = service.drain_pending(
        db, dispatch=lambda run_id: dispatched.append(run_id),
        lease_owner="event-dispatcher", now=FIXED_NOW + timedelta(minutes=1),
    )

    assert dispatched == [receipt.run_id]
    assert recovered[0].run_id == receipt.run_id
    assert db.get(RefreshRun, receipt.run_id).dispatch_state == "dispatched"
    assert service.process(db, run_id=receipt.run_id, lease_owner="event-worker-001", now=FIXED_NOW).status == "succeeded"


def test_second_event_waits_for_source_lease_then_drains(db):
    _source(db)
    service = EventIngestService()
    first = ManagedOutboxAdapter.normalize(_record(event_id="evt-first"), source_id="source-001", received_at=FIXED_NOW)
    second = ManagedOutboxAdapter.normalize(_record(event_id="evt-second", watermark="2026-08-26T02:00:00Z"), source_id="source-001", received_at=FIXED_NOW)

    first_receipt = service.accept(db, first, lease_owner="event-worker-001", now=FIXED_NOW)
    second_receipt = service.accept(db, second, lease_owner="event-worker-002", now=FIXED_NOW)

    assert second_receipt.status == "received"
    assert second_receipt.run_id is None
    assert db.query(RefreshInboxEvent).filter(RefreshInboxEvent.event_id == "evt-second").one().state == "received"

    service.process(db, run_id=first_receipt.run_id, lease_owner="event-worker-001", now=FIXED_NOW)
    dispatched: list[str] = []
    recovered = service.drain_pending(
        db, dispatch=lambda run_id: dispatched.append(run_id),
        lease_owner="event-dispatcher", now=FIXED_NOW + timedelta(minutes=1),
    )

    assert dispatched and recovered[0].event_id == "evt-second"
    second_run_id = recovered[0].run_id
    assert second_run_id is not None
    assert service.process(db, run_id=second_run_id, lease_owner="event-worker-002", now=FIXED_NOW + timedelta(minutes=1)).status == "succeeded"


def test_lease_contended_event_with_later_config_drift_is_dead_lettered_and_replayable(db):
    _source(db, schema_hash="schema-v1")
    service = EventIngestService()
    first = ManagedOutboxAdapter.normalize(_record(event_id="evt-first"), source_id="source-001", received_at=FIXED_NOW)
    second = ManagedOutboxAdapter.normalize(
        _record(event_id="evt-second", watermark="2026-08-26T02:00:00Z"),
        source_id="source-001", received_at=FIXED_NOW,
    )

    first_receipt = service.accept(db, first, lease_owner="event-worker-001", now=FIXED_NOW)
    pending = service.accept(db, second, lease_owner="event-worker-002", now=FIXED_NOW)
    assert pending.run_id is None

    update_source_configuration(
        db, source_id="source-001", resource="orders", cursor_contract="watermark_primary_key",
        configuration={"schema_hash": "schema-v2"}, now=FIXED_NOW + timedelta(seconds=1),
    )
    drained = service.drain_pending(
        db, dispatch=lambda _run_id: None, lease_owner="event-dispatcher", now=FIXED_NOW + timedelta(minutes=1),
        source_id="source-001", resource="orders",
    )

    second_drained = next(receipt for receipt in drained if receipt.event_id == "evt-second")
    assert second_drained.status == "dead_lettered"
    assert second_drained.reason_code == "SCHEMA_DRIFT"
    assert second_drained.dead_letter_id is not None
    inbox = db.query(RefreshInboxEvent).filter(RefreshInboxEvent.event_id == "evt-second").one()
    assert inbox.state == "dead_lettered"
    assert inbox.run_id is None
    assert db.query(RefreshRun).filter(RefreshRun.id == first_receipt.run_id).one().status == "running"
    assert db.query(RefreshDeadLetter).filter(RefreshDeadLetter.event_id == "evt-second").count() == 1
    assert db.query(RefreshSourceState).one().cursor is None

    replay = service.replay_dead_letter(
        db, dead_letter_id=second_drained.dead_letter_id, operator_id="operator-001", now=FIXED_NOW + timedelta(minutes=2),
    )

    assert replay.id != first_receipt.run_id
    assert replay.trigger == "replay"
    replayed_inbox = db.query(RefreshInboxEvent).filter(RefreshInboxEvent.event_id == "evt-second").one()
    assert replayed_inbox.state == "received"
    assert replayed_inbox.run_id == replay.id
    assert db.query(RefreshDeadLetter).filter(RefreshDeadLetter.event_id == "evt-second").one().replay_run_id == replay.id


def test_broker_publish_failures_exhaust_delivery_budget_into_replayable_dlq(db):
    _source(db)
    service = EventIngestService(max_attempts=2)
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    fail = lambda _run_id: (_ for _ in ()).throw(RuntimeError("broker down"))

    first = service.dispatch_pending(db, run_id=receipt.run_id, dispatch=fail, now=FIXED_NOW)
    assert first.status == "publish_failed"
    assert db.query(RefreshInboxEvent).one().delivery_attempts == 1
    assert db.query(RefreshDeadLetter).count() == 0
    retry_at = db.get(RefreshRun, receipt.run_id).dispatch_retry_at
    assert retry_at == (FIXED_NOW + timedelta(seconds=15)).replace(tzinfo=None)

    second = service.dispatch_pending(
        db, run_id=receipt.run_id, dispatch=fail, now=FIXED_NOW + timedelta(seconds=16),
    )

    assert second.status == "dead_lettered"
    assert second.reason_code == "PUBLISH_EXHAUSTED"
    assert second.dead_letter_id is not None
    assert db.query(RefreshInboxEvent).one().state == "dead_lettered"
    assert db.query(RefreshInboxEvent).one().delivery_attempts == 2
    exhausted_run = db.get(RefreshRun, receipt.run_id)
    assert exhausted_run.status == "dead_lettered"
    assert exhausted_run.retry_reason == "PUBLISH_EXHAUSTED"
    dead_letter = db.query(RefreshDeadLetter).one()
    assert dead_letter.reason == "PUBLISH_EXHAUSTED"
    assert dead_letter.delivery_attempts == 2

    replay = service.replay_dead_letter(
        db, dead_letter_id=dead_letter.id, operator_id="operator-001", now=FIXED_NOW + timedelta(minutes=1),
    )
    assert replay.id != receipt.run_id
    assert db.query(RefreshRun).filter(RefreshRun.idempotency_key == f"replay:event:{dead_letter.id}").count() == 1
    assert db.query(RefreshInboxEvent).one().state == "received"


def test_dead_letter_event_yields_to_a_committed_cancellation(db):
    """An operator's committed `cancel_requested` must win over a late
    retry-exhaustion failure that reaches `_dead_letter_event` afterward
    (e.g. a connector call that was already in flight when the cancellation
    landed, then raised a genuine failure post-hoc). Before the fix, the old
    guard set `{"succeeded", "failed", "dead_lettered", "cancelled"}` did not
    include `cancel_requested`, so this call would overwrite it with
    `dead_lettered` and create a `RefreshDeadLetter` row an operator could
    replay — resurrecting a run the operator explicitly cancelled."""
    _source(db)
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(event_id="evt-cancel-race"), source_id="source-001", received_at=FIXED_NOW)
    receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    assert receipt.run_id is not None
    assert db.get(RefreshRun, receipt.run_id).status == "running"

    cancelled = request_refresh_cancellation(
        db, run_id=receipt.run_id, requested_by="operator-001", reason="stop", now=FIXED_NOW,
    )
    assert cancelled.status == "cancel_requested"

    result = service._dead_letter_event(db, run_id=receipt.run_id, reason="RETRY_EXHAUSTED", now=FIXED_NOW)

    assert result.status == "cancelled"
    assert result.retry_reason == "RETRY_EXHAUSTED"
    assert db.query(RefreshDeadLetter).filter(RefreshDeadLetter.run_id == receipt.run_id).count() == 0


def test_dlq_replay_recovers_a_run_persisted_before_crash(db, monkeypatch):
    _source(db)
    service = EventIngestService(max_attempts=1)
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    service.dead_letter(db, run_id=receipt.run_id, reason="fixture failure", now=FIXED_NOW)
    dead_letter_id = db.query(service.dead_letter_model).one().id
    original_claim = event_ingest.claim_refresh_run

    def claim_then_crash(*args, **kwargs):
        original_claim(*args, **kwargs)
        raise RuntimeError("crash after replay run commit")

    monkeypatch.setattr(event_ingest, "claim_refresh_run", claim_then_crash)
    with pytest.raises(RuntimeError):
        service.replay_dead_letter(db, dead_letter_id=dead_letter_id, operator_id="operator-001", now=FIXED_NOW)

    monkeypatch.setattr(event_ingest, "claim_refresh_run", original_claim)
    recovered = service.replay_dead_letter(
        db, dead_letter_id=dead_letter_id, operator_id="operator-002", now=FIXED_NOW + timedelta(minutes=1),
    )

    assert db.query(RefreshRun).filter(RefreshRun.idempotency_key == f"replay:event:{dead_letter_id}").count() == 1
    assert db.get(service.dead_letter_model, dead_letter_id).replay_run_id == recovered.id
    assert recovered.trigger == "replay"


def test_duplicate_redelivery_bypasses_new_schema_revision_but_new_event_does_not(db):
    _source(db, schema_hash="schema-v1")
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    first = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)
    update_source_configuration(
        db, source_id="source-001", resource="orders", cursor_contract="watermark_primary_key",
        configuration={"schema_hash": "schema-v2"}, now=FIXED_NOW + timedelta(seconds=1),
    )

    duplicate = service.accept(db, envelope, lease_owner="event-worker-002", now=FIXED_NOW + timedelta(seconds=2))

    assert duplicate.status == "duplicate"
    assert duplicate.run_id == first.run_id
    new_event = ManagedOutboxAdapter.normalize(
        _record(event_id="evt-new"), source_id="source-001", received_at=FIXED_NOW,
    )
    with pytest.raises(EventIngressError) as exc:
        service.accept(db, new_event, lease_owner="event-worker-002", now=FIXED_NOW + timedelta(seconds=2))
    assert exc.value.reason_code == "SCHEMA_DRIFT"


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


def test_event_config_drift_late_finish_has_no_lineage_or_cursor_progress(db):
    """Mirrors polling's `test_configuration_drift_after_source_pull_is_typed_and_has_no_lineage`
    for the event path: the source configuration is bumped after the event's
    run was claimed but before `process()` finishes, so the frozen
    config_version/cursor_contract no longer matches the live source
    revision by the time materialization would happen."""
    _source(db, schema_hash="schema-v1")
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)

    update_source_configuration(
        db, source_id="source-001", resource="orders", cursor_contract="watermark_primary_key",
        configuration={"schema_hash": "schema-v2"}, now=FIXED_NOW + timedelta(seconds=1),
    )

    with pytest.raises(ConfigurationDriftError) as exc:
        service.process(db, run_id=receipt.run_id, lease_owner="event-worker-001", now=FIXED_NOW + timedelta(seconds=2))

    assert exc.value.reason_code == "CONFIGURATION_DRIFT"
    assert db.query(DatasetVersion).count() == 0
    assert db.query(PipelineRun).count() == 0
    assert db.query(RefreshSourceState).one().cursor is None


def test_event_out_of_order_cursor_is_unchanged_with_late_provenance(db):
    """Mirrors polling's "out-of-order" case for the event path: an event
    whose cursor is behind the source's already-advanced cursor must not
    regress the cursor, and is recorded as late in provenance/late_count."""
    _source(db)
    service = EventIngestService()
    first = ManagedOutboxAdapter.normalize(
        _record(event_id="evt-first", watermark="2026-08-26T02:00:00Z"),
        source_id="source-001", received_at=FIXED_NOW,
    )
    first_receipt = service.accept(db, first, lease_owner="event-worker-001", now=FIXED_NOW)
    first_result = service.process(db, run_id=first_receipt.run_id, lease_owner="event-worker-001", now=FIXED_NOW)
    assert first_result.status == "succeeded"
    assert first_result.cursor_after.watermark == "2026-08-26T02:00:00Z"

    late = ManagedOutboxAdapter.normalize(
        _record(event_id="evt-late", watermark="2026-08-26T01:00:00Z"),
        source_id="source-001", received_at=FIXED_NOW,
    )
    late_receipt = service.accept(db, late, lease_owner="event-worker-002", now=FIXED_NOW + timedelta(seconds=1))
    late_result = service.process(
        db, run_id=late_receipt.run_id, lease_owner="event-worker-002", now=FIXED_NOW + timedelta(seconds=1),
    )

    assert late_result.status == "succeeded"
    assert late_result.late_event_count == 1
    assert late_result.provenance["cursor_outcome"] == "unchanged"
    assert late_result.cursor_after == late_result.cursor_before
    assert late_result.cursor_after.watermark == "2026-08-26T02:00:00Z"


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


def test_event_process_propagates_soft_time_limit_exceeded_instead_of_swallowing_it(db, monkeypatch):
    """A Celery soft-timeout raised deep inside ``process()`` must propagate to
    ``_refresh_event``'s dedicated ``except SoftTimeLimitExceeded`` handler,
    not be caught by ``process()``'s own blanket ``except Exception`` and
    turned into a counted-against-retry-budget failure/dead-letter."""
    from celery.exceptions import SoftTimeLimitExceeded

    _source(db)
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(_record(), source_id="source-001", received_at=FIXED_NOW)
    receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=FIXED_NOW)

    def raise_soft_timeout(*_args, **_kwargs):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(event_ingest.polling, "_resolve_dataset", raise_soft_timeout)

    with pytest.raises(SoftTimeLimitExceeded):
        service.process(db, run_id=receipt.run_id, lease_owner="event-worker-001", now=FIXED_NOW)

    # The run must remain in its pre-timeout state: no retry/dead-letter
    # bookkeeping was performed by `process()` itself for this exception.
    db.rollback()
    run = db.get(RefreshRun, receipt.run_id)
    assert run.status == "running"
    assert run.retry_count == 0
    assert db.query(RefreshDeadLetter).count() == 0


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


def test_webhook_route_reports_durable_publish_failure_for_later_drain(client, db, monkeypatch):
    _source(db)
    db.get(Connection, "source-001").config = {
        "cursor_contract": "watermark_primary_key", "webhook_secret_ref": SECRET,
    }
    db.commit()
    from app.tasks.v2 import refresh_tasks

    def broker_down(_run_id):
        raise RuntimeError("broker down")

    monkeypatch.setattr(refresh_tasks.refresh_event_task, "delay", broker_down)
    body = _body(event_id="evt-broker-down")
    timestamp = datetime.now(timezone.utc).isoformat()
    response = client.post(
        "/api/v2/refresh/events/webhook/source-001",
        content=body,
        headers={"X-Webhook-Signature": _signature(body, timestamp=timestamp), "X-Webhook-Timestamp": timestamp},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "publish_failed"
    run = db.query(RefreshRun).one()
    assert run.dispatch_state == "publish_failed"
    assert db.query(RefreshInboxEvent).one().state == "received"


def test_webhook_duplicate_acknowledgement_precedes_schema_validation(client, db, monkeypatch):
    _source(db)
    db.get(Connection, "source-001").config = {
        "cursor_contract": "watermark_primary_key", "webhook_secret_ref": SECRET,
    }
    db.commit()
    from app.tasks.v2 import refresh_tasks

    published: list[str] = []
    monkeypatch.setattr(refresh_tasks.refresh_event_task, "delay", published.append)
    body = _body(event_id="evt-route-duplicate")
    timestamp = datetime.now(timezone.utc).isoformat()
    headers = {
        "X-Webhook-Signature": _signature(body, timestamp=timestamp),
        "X-Webhook-Timestamp": timestamp,
    }
    first = client.post("/api/v2/refresh/events/webhook/source-001", content=body, headers=headers)
    assert first.status_code == 200

    update_source_configuration(
        db, source_id="source-001", resource="orders", cursor_contract="watermark_primary_key",
        configuration={"schema_hash": "schema-v2", "webhook_secret_ref": SECRET},
        now=FIXED_NOW + timedelta(seconds=1),
    )
    duplicate = client.post("/api/v2/refresh/events/webhook/source-001", content=body, headers=headers)

    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["run_id"] == first.json()["run_id"]
    assert published == [first.json()["run_id"]]


def test_webhook_route_decrypts_managed_connection_config_for_secret_ref(client, db, monkeypatch):
    """POST /api/v2/connections (app/routers/v2/connections.py's
    create_connection) always persists `connection.config` encrypted:
    `{"_encrypted": encryption_service.encrypt(json.dumps(body.config))}`.
    This test replicates that exact encryption call (rather than hand-setting
    plaintext `connection.config`, as every other webhook test in this file
    does) so the webhook ingress is proven against the REAL persisted shape.
    Before the fix, `_source_secret_ref` read `connection.config` raw and
    never found `webhook_secret_ref` inside the `_encrypted` wrapper, so
    every managed webhook source configured through the primary Connections
    API always 503'd."""
    from app.services import encryption_service

    db.add(Connection(
        id="source-001", name="orders-source", kind="rest", status="active",
        config={"_encrypted": encryption_service.encrypt(json.dumps({
            "cursor_contract": "watermark_primary_key", "webhook_secret_ref": SECRET,
        }))},
    ))
    db.add(RefreshSourceState(
        id="state-001", source_id="source-001", resource="orders",
        cursor_contract="watermark_primary_key", config_version=1,
        configuration={"schema_hash": "schema-v1"}, fencing_token=0,
    ))
    db.commit()

    from app.tasks.v2 import refresh_tasks

    monkeypatch.setattr(refresh_tasks.refresh_event_task, "delay", lambda run_id: None)
    body = _body(event_id="evt-encrypted-001")
    timestamp = datetime.now(timezone.utc).isoformat()
    response = client.post(
        "/api/v2/refresh/events/webhook/source-001",
        content=body,
        headers={"X-Webhook-Signature": _signature(body, timestamp=timestamp), "X-Webhook-Timestamp": timestamp},
    )

    assert response.status_code == 200, response.text
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


def test_accept_concurrent_brand_new_event_id_yields_one_row_and_graceful_duplicate(concurrent_event_db):
    """`accept()`'s `SELECT ... FOR UPDATE` on RefreshInboxEvent only locks
    *existing* rows. Two genuinely concurrent deliveries of the same
    brand-new event identity can both observe `row is None` and race on the
    insert; the loser must fold into the same durable `duplicate` outcome
    instead of surfacing an unhandled IntegrityError.

    No RefreshSourceState is pre-seeded: if it already existed, `accept()`'s
    own `SELECT ... FOR UPDATE` on that row would serialize the two threads
    long before the RefreshInboxEvent insert and the race would never occur.
    Leaving the state unseeded lets both threads race genuinely, matching
    a real pair of concurrent first-time webhook deliveries for a source.
    """
    seed = concurrent_event_db.new_session()
    try:
        seed.add(Connection(
            id="source-001", name="orders-source", kind="rest",
            config={"cursor_contract": "watermark_primary_key"}, status="active",
        ))
        seed.commit()
    finally:
        seed.close()

    envelope = ManagedOutboxAdapter.normalize(
        _record(event_id="evt-race"), source_id="source-001", received_at=FIXED_NOW,
    )
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: dict[str, Exception] = {}
    lock = threading.Lock()

    def attempt(owner: str) -> None:
        session = concurrent_event_db.new_session()
        try:
            barrier.wait(timeout=10)
            receipt = EventIngestService().accept(session, envelope, lease_owner=owner, now=FIXED_NOW)
            with lock:
                results[owner] = receipt
        except Exception as exc:  # pragma: no cover - captured as a failure below
            with lock:
                errors[owner] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=attempt, args=(owner,)) for owner in ("racer-a", "racer-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"expected graceful duplicate handling for both racers, got errors: {errors!r}"
    assert len(results) == 2
    statuses = sorted(receipt.status for receipt in results.values())
    assert statuses == ["duplicate", "received"]

    verify = concurrent_event_db.new_session()
    try:
        rows = verify.query(RefreshInboxEvent).filter(RefreshInboxEvent.event_id == "evt-race").all()
        assert len(rows) == 1
        assert rows[0].state == "duplicate"
    finally:
        verify.close()
