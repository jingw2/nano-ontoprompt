"""Task 8 polling safe-point cancellation tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session

from app.models.v2.dataset import DatasetVersion
from app.models.v2.pipeline import PipelineRunInput
from app.models.v2.refresh import RefreshRun
from app.schemas.refresh import RefreshPolicy
from app.services.v2.incremental import polling
from app.services.v2.incremental.contract import claim_refresh_run, request_refresh_cancellation
from app.services.v2.incremental.event_adapters import ManagedOutboxAdapter
from app.services.v2.incremental.event_ingest import EventIngestService
from app.services.v2.incremental.polling import poll_source

from .test_refresh_polling import _SourceConnector, _cursor, _envelope, _polling_source


NOW = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc)


def _claim(db, *, owner: str = "worker-001") -> RefreshRun:
    return claim_refresh_run(
        db,
        source_id="source-001",
        resource="orders",
        policy=RefreshPolicy.MICRO_BATCH,
        idempotency_key="cancellation-test",
        lease_owner=owner,
        now=NOW,
    )


def test_cancel_before_pull_finalizes_without_connector_call(db):
    connector = _SourceConnector(page=None)
    _polling_source(db, connector=connector)
    run = _claim(db)
    request_refresh_cancellation(
        db, run_id=run.id, requested_by="operator-001", reason="maintenance", now=NOW,
    )

    result = poll_source(
        db, source_id="source-001", resource="orders", lease_owner="worker-001",
        now=NOW, _run_id=run.id,
    )

    assert result.status == "cancelled"
    assert result.cancel_requested_at is not None
    assert connector.calls == []
    assert result.cursor_before == result.cursor_after


def test_cancel_at_inflight_page_stops_before_materialization(db):
    event = _envelope("evt-1", "2026-08-26T01:00:00Z", "100", {"id": "100"})
    connector = _SourceConnector(page=polling.DeltaPage(
        envelopes=[event], candidate_cursor=_cursor("2026-08-26T01:00:00Z", "100"),
        source_observed_at=NOW, source_lag_seconds=1,
    ))
    _polling_source(db, connector=connector)

    original_pull = connector.pull_delta

    def pull_and_cancel(resource, *, cursor, overlap_window):
        page = original_pull(resource, cursor=cursor, overlap_window=overlap_window)
        run = db.query(RefreshRun).order_by(RefreshRun.created_at.desc()).first()
        request_refresh_cancellation(
            db, run_id=run.id, requested_by="operator-001", reason="stop", now=NOW,
        )
        return page

    connector.pull_delta = pull_and_cancel
    result = poll_source(
        db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW,
    )

    assert result.status == "cancelled"
    assert result.input_dataset_version_ids == []
    assert result.pipeline_run_id is None
    assert db.query(DatasetVersion).count() == 0
    assert db.query(PipelineRunInput).count() == 0


def test_cancel_after_tentative_materialization_rolls_back_lineage(db, monkeypatch):
    if db.get_bind().dialect.name == "sqlite":
        pytest.skip("SQLite database-level write locks cannot model the concurrent safe-point race")
    event = _envelope("evt-1", "2026-08-26T01:00:00Z", "100", {"id": "100"})
    connector = _SourceConnector(page=polling.DeltaPage(
        envelopes=[event], candidate_cursor=_cursor("2026-08-26T01:00:00Z", "100"),
        source_observed_at=NOW, source_lag_seconds=1,
    ))
    _polling_source(db, connector=connector)
    original = polling.record_refresh_outcome

    def cancel_before_outcome(session, **kwargs):
        external = Session(bind=session.get_bind())
        try:
            run = external.get(RefreshRun, kwargs["run_id"])
            request_refresh_cancellation(
                external, run_id=run.id, requested_by="operator-001", reason="stop", now=NOW,
            )
        finally:
            external.close()
        return original(session, **kwargs)

    monkeypatch.setattr(polling, "record_refresh_outcome", cancel_before_outcome)
    result = poll_source(
        db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW,
    )

    assert result.status == "cancelled"
    assert result.cursor_before == result.cursor_after
    assert result.input_dataset_version_ids == []
    assert result.pipeline_run_id is None
    assert db.query(DatasetVersion).count() == 0
    assert db.query(PipelineRunInput).count() == 0


def test_cancellation_after_success_is_plain_already_terminal(db):
    event = _envelope("evt-1", "2026-08-26T01:00:00Z", "100", {"id": "100"})
    connector = _SourceConnector(page=polling.DeltaPage(
        envelopes=[event], candidate_cursor=_cursor("2026-08-26T01:00:00Z", "100"),
        source_observed_at=NOW, source_lag_seconds=1,
    ))
    _polling_source(db, connector=connector)
    result = poll_source(
        db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW,
    )
    run = db.get(RefreshRun, result.run_id)

    cancelled = request_refresh_cancellation(
        db, run_id=run.id, requested_by="operator-001", reason="too late", now=NOW + timedelta(seconds=1),
    )

    assert cancelled.already_terminal is True
    assert cancelled.status == "succeeded"
    assert result.input_dataset_version_ids
    assert result.pipeline_run_id is not None


def test_event_cancel_before_materialization_preserves_inbox_and_cursor(db):
    event = _envelope("evt-event-1", "2026-08-26T01:00:00Z", "100", {"id": "100"})
    _polling_source(db, connector=_SourceConnector(page=None))
    service = EventIngestService()
    receipt = service.accept(db, event, lease_owner="event-worker-001", now=NOW)
    request_refresh_cancellation(
        db, run_id=receipt.run_id, requested_by="operator-001", reason="stop", now=NOW,
    )

    result = service.process(db, run_id=receipt.run_id, lease_owner="event-worker-001", now=NOW)

    assert result.status == "cancelled"
    assert result.cursor_after == result.cursor_before
    assert result.input_dataset_version_ids == []


def test_event_outbox_envelope_uses_the_same_cancellation_contract(db):
    _polling_source(db, connector=_SourceConnector(page=None))
    service = EventIngestService()
    envelope = ManagedOutboxAdapter.normalize(
        {
            "version": 1, "event_id": "evt-event-2", "source_id": "source-001",
            "resource": "orders", "operation": "upsert", "primary_key": "101",
            "payload": {"id": "101"}, "watermark": "2026-08-26T01:00:00Z",
            "schema_hash": "schema-v1", "occurred_at": NOW.isoformat(),
        }, source_id="source-001", received_at=NOW,
    )
    receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=NOW)
    result = service.process(db, run_id=receipt.run_id, lease_owner="event-worker-001", now=NOW)

    cancelled = request_refresh_cancellation(
        db, run_id=result.run_id, requested_by="operator-001", reason="too late", now=NOW,
    )
    assert result.status == "succeeded"
    assert cancelled.already_terminal is True
    assert cancelled.status == "succeeded"
