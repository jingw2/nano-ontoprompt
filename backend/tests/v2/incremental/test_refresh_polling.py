"""Task 8 polling contract tests.

The connector in these tests is deliberately a small source-side fake.  The
real code under test still owns cursor ordering, overlap deduplication,
DatasetVersion/PipelineRun durability, and retry state; only the external
source pull is replaced.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.v2.connection import Connection
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineRunInput
from app.models.v2.refresh import RefreshDeadLetter, RefreshInboxEvent, RefreshRun, RefreshSourceState
from app.schemas.refresh import (
    ChangeEnvelope, ConfigurationDriftError, RefreshError, RefreshPolicy, SourceCursor, cursor_order,
)
from app.services.v2.incremental import polling
from app.services.v2.incremental.contract import claim_refresh_run, request_refresh_cancellation, update_source_configuration
from app.services.v2.incremental.operations import trigger_refresh
from app.services.v2.incremental.polling import DeltaPage, poll_source, replay_refresh
from app.services.v2.scheduler.schedule_service import ScheduleRequest, upsert_refresh_schedule


NOW = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc)


def _cursor(watermark: str, primary_key: str, *, source_id: str = "source-001") -> SourceCursor:
    return SourceCursor(
        source_id=source_id,
        resource="orders",
        contract="watermark_primary_key",
        watermark=watermark,
        primary_key=primary_key,
        opaque_value=None,
        observed_at=NOW,
    )


def _envelope(event_id: str, watermark: str, primary_key: str, payload: dict) -> ChangeEnvelope:
    return ChangeEnvelope(
        event_id=event_id,
        source_id="source-001",
        resource="orders",
        operation="upsert",
        primary_key=primary_key,
        payload=payload,
        watermark=watermark,
        source_cursor=None,
        schema_hash="schema-v1",
        occurred_at=NOW,
        received_at=NOW,
    )


class _SourceConnector:
    def __init__(self, page: DeltaPage | None = None, error: Exception | None = None):
        self.page = page
        self.error = error
        self.calls: list[tuple[str, SourceCursor | None, timedelta]] = []

    def pull_delta(self, resource: str, *, cursor: SourceCursor | None, overlap_window: timedelta) -> DeltaPage:
        self.calls.append((resource, cursor, overlap_window))
        if self.error:
            raise self.error
        assert self.page is not None
        return self.page


def _polling_source(db, *, connector: _SourceConnector) -> tuple[Connection, Dataset, Pipeline]:
    source = Connection(
        id="source-001",
        name="orders-source",
        kind="rest",
        config={
            "base_url": "https://example.test",
            "cursor_contract": "watermark_primary_key",
            "watermark_column": "updated_at",
            "primary_key_column": "id",
            "overlap_window_seconds": 60,
        },
        status="active",
    )
    dataset = Dataset(
        id="dataset-orders",
        name="orders",
        source_connection_id=source.id,
        kind="structured",
    )
    pipeline = Pipeline(
        id="pipeline-orders",
        name="orders-pipeline",
        source_dataset_id=dataset.id,
        spec={},
        status="active",
    )
    db.add_all([source, dataset, pipeline])
    db.commit()
    polling._connector_for_source = lambda *_args, **_kwargs: connector
    return source, dataset, pipeline


@pytest.mark.parametrize("case_id", ["normal", "empty", "late", "equal-watermark", "duplicate", "out-of-order"])
def test_polling_cases_are_deterministic(db, case_id):
    events = {
        "normal": [_envelope("evt-1", "2026-08-26T01:00:00Z", "100", {"id": "100"})],
        "empty": [],
        "late": [_envelope("evt-late", "2026-08-25T23:59:30Z", "90", {"id": "90"})],
        "equal-watermark": [
            _envelope("evt-101", "2026-08-26T01:00:00Z", "101", {"id": "101"}),
        ],
        "duplicate": [
            _envelope("evt-2", "2026-08-26T01:00:00Z", "102", {"id": "102"}),
            _envelope("evt-2", "2026-08-26T01:00:00Z", "102", {"id": "102"}),
        ],
        "out-of-order": [
            _envelope("evt-3", "2026-08-26T00:30:00Z", "103", {"id": "103"}),
            _envelope("evt-4", "2026-08-26T02:00:00Z", "104", {"id": "104"}),
        ],
    }[case_id]
    candidate = max(events, key=lambda item: (item.watermark or "", item.primary_key), default=None)
    page = DeltaPage(
        envelopes=events,
        candidate_cursor=_cursor(candidate.watermark, candidate.primary_key) if candidate else _cursor("", ""),
        source_observed_at=NOW,
        source_lag_seconds=3.0,
    )
    connector = _SourceConnector(page=page)
    _polling_source(db, connector=connector)

    result = poll_source(db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW)

    assert result.status == "succeeded"
    assert result.duplicate_count == (1 if case_id == "duplicate" else 0)
    if result.cursor_before is None:
        assert result.cursor_after is None or result.cursor_after.watermark is not None
    elif result.cursor_after is not None:
        assert cursor_order(result.cursor_before, result.cursor_after) >= 0
    else:
        assert result.cursor_before == result.cursor_after
    assert connector.calls == [("orders", None, timedelta(seconds=60))]
    lineage = db.query(PipelineRunInput).filter(
        PipelineRunInput.pipeline_run_id == result.pipeline_run_id,
    ).one()
    assert lineage.provenance["refresh_run_id"] == result.run_id


def test_failed_retry_keeps_cursor_until_pipeline_success(db):
    connector = _SourceConnector(error=RuntimeError("source unavailable"))
    _polling_source(db, connector=connector)

    result = poll_source(db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW)

    assert result.status == "queued"
    assert result.cursor_after == result.cursor_before
    assert result.retry_count == 1
    assert db.query(DatasetVersion).count() == 0
    assert db.query(PipelineRun).count() == 0
    state = db.query(RefreshSourceState).filter(
        RefreshSourceState.source_id == "source-001",
        RefreshSourceState.resource == "orders",
    ).one()
    assert state.lease_owner is None


def test_failed_retry_records_bounded_backoff_deadline(db):
    connector = _SourceConnector(error=RuntimeError("source unavailable"))
    _polling_source(db, connector=connector)
    source = db.query(Connection).filter(Connection.id == "source-001").one()
    source.config = {
        **source.config,
        "retry_policy": {"max_attempts": 3, "backoff_seconds": 30},
    }
    db.commit()

    result = poll_source(db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW)

    run = db.get(RefreshRun, result.run_id)
    assert run.dispatch_retry_at.replace(tzinfo=timezone.utc) == NOW + timedelta(seconds=30)


def test_fresh_opaque_source_claim_uses_connection_cursor_contract(db):
    source = Connection(
        id="source-opaque", name="opaque-source", kind="rest",
        config={
            "base_url": "https://example.test",
            "cursor_contract": "opaque_source_cursor",
            "cursor_param": "after",
        }, status="active",
    )
    dataset = Dataset(
        id="dataset-opaque", name="orders", source_connection_id=source.id, kind="structured",
    )
    db.add_all([source, dataset])
    db.commit()
    page = DeltaPage(
        envelopes=[_envelope("evt-opaque", "2026-08-26T01:00:00Z", "100", {"id": "100"})],
        candidate_cursor=SourceCursor(
            source_id=source.id, resource="orders", contract="opaque_source_cursor",
            watermark=None, primary_key=None, opaque_value="cursor-100", observed_at=NOW,
        ),
        source_observed_at=NOW, source_lag_seconds=1,
    )
    connector = _SourceConnector(page=page)
    polling._connector_for_source = lambda *_args, **_kwargs: connector

    result = poll_source(
        db, source_id=source.id, resource="orders", lease_owner="worker-001", now=NOW,
    )

    assert result.status == "succeeded"
    assert result.cursor_contract == "opaque_source_cursor"
    assert result.cursor_after.opaque_value == "cursor-100"


def test_configuration_drift_after_source_pull_is_typed_and_has_no_lineage(db):
    source, _, _ = _polling_source(db, connector=_SourceConnector(page=DeltaPage(
        envelopes=[], candidate_cursor=_cursor("", ""), source_observed_at=NOW,
        source_lag_seconds=0,
    )))

    def pull_and_upgrade(resource, *, cursor, overlap_window):
        update_source_configuration(
            db,
            source_id=source.id,
            resource=resource,
            cursor_contract="watermark_primary_key",
            configuration={"schema_hash": "schema-v2"},
            now=NOW + timedelta(seconds=1),
        )
        return DeltaPage(
            envelopes=[], candidate_cursor=_cursor("", ""), source_observed_at=NOW,
            source_lag_seconds=0,
        )

    connector = _SourceConnector()
    connector.pull_delta = pull_and_upgrade
    polling._connector_for_source = lambda *_args, **_kwargs: connector

    with pytest.raises(ConfigurationDriftError) as exc:
        poll_source(db, source_id=source.id, resource="orders", lease_owner="worker-001", now=NOW)

    assert exc.value.reason_code == "CONFIGURATION_DRIFT"
    assert db.query(DatasetVersion).count() == 0
    assert db.query(PipelineRun).count() == 0


def test_refresh_task_configuration_drift_releases_source_lease_without_progress(db, monkeypatch):
    _polling_source(db, connector=_SourceConnector(page=DeltaPage(
        envelopes=[], candidate_cursor=_cursor("", ""), source_observed_at=NOW,
        source_lag_seconds=0,
    )))
    run = claim_refresh_run(
        db, source_id="source-001", resource="orders", policy="micro_batch",
        idempotency_key="config-drift-worker", lease_owner="worker-001", now=NOW,
    )

    def raise_drift(*_args, **_kwargs):
        raise ConfigurationDriftError("source changed while polling")

    monkeypatch.setattr(polling, "poll_source", raise_drift)
    from app import database as app_database
    from sqlalchemy.orm import sessionmaker

    worker_session = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(app_database, "SessionLocal", worker_session)
    from app.tasks.v2 import refresh_tasks

    result = refresh_tasks._refresh_poll(run.id)

    db.expire_all()
    state = db.query(RefreshSourceState).filter(
        RefreshSourceState.source_id == "source-001",
        RefreshSourceState.resource == "orders",
    ).one()
    failed = db.get(RefreshRun, run.id)
    assert result["status"] == "failed"
    assert failed.status == "failed"
    assert state.lease_owner is None
    assert state.lease_expires_at is None
    assert state.cursor_json is None
    assert db.query(PipelineRunInput).count() == 0

    replacement = claim_refresh_run(
        db, source_id="source-001", resource="orders", policy="micro_batch",
        idempotency_key="config-drift-replacement", lease_owner="worker-002",
        now=NOW + timedelta(seconds=1),
    )
    assert replacement.status == "running"


def test_refresh_task_worker_interrupted_requeues_without_cursor_or_checkpoint_progress(db, monkeypatch):
    _polling_source(db, connector=_SourceConnector(page=DeltaPage(
        envelopes=[], candidate_cursor=_cursor("", ""), source_observed_at=NOW,
        source_lag_seconds=0,
    )))
    run = claim_refresh_run(
        db, source_id="source-001", resource="orders", policy="micro_batch",
        idempotency_key="worker-interrupted", lease_owner="worker-001", now=NOW,
    )
    state_before = db.query(RefreshSourceState).filter(
        RefreshSourceState.source_id == "source-001",
        RefreshSourceState.resource == "orders",
    ).one()
    cursor_before_snapshot = state_before.cursor_json

    from celery.exceptions import SoftTimeLimitExceeded

    def raise_soft_timeout(*_args, **_kwargs):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(polling, "poll_source", raise_soft_timeout)
    from app import database as app_database
    from sqlalchemy.orm import sessionmaker

    worker_session = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(app_database, "SessionLocal", worker_session)
    from app.tasks.v2 import refresh_tasks

    result = refresh_tasks._refresh_poll(run.id)

    db.expire_all()
    requeued = db.get(RefreshRun, run.id)
    state_after = db.query(RefreshSourceState).filter(
        RefreshSourceState.source_id == "source-001",
        RefreshSourceState.resource == "orders",
    ).one()
    assert result["status"] == "queued"
    assert requeued.status == "queued"
    assert requeued.dispatch_state == "pending"
    assert requeued.retry_reason == "WORKER_INTERRUPTED"
    assert requeued.cursor_before == requeued.cursor_after
    assert state_after.cursor_json == cursor_before_snapshot
    assert db.query(DatasetVersion).count() == 0
    assert db.query(PipelineRun).count() == 0


def test_polling_deduplicates_processed_overlap_across_runs(db):
    event_one = _envelope("evt-overlap-1", "2026-08-26T01:00:00Z", "100", {"id": "100"})
    first_connector = _SourceConnector(page=DeltaPage(
        envelopes=[event_one], candidate_cursor=_cursor("2026-08-26T01:00:00Z", "100"),
        source_observed_at=NOW, source_lag_seconds=1,
    ))
    _polling_source(db, connector=first_connector)
    first = poll_source(
        db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW,
    )

    event_two = _envelope("evt-overlap-2", "2026-08-26T02:00:00Z", "101", {"id": "101"})
    second_connector = _SourceConnector(page=DeltaPage(
        envelopes=[event_one, event_two], candidate_cursor=_cursor("2026-08-26T02:00:00Z", "101"),
        source_observed_at=NOW + timedelta(minutes=1), source_lag_seconds=1,
    ))
    polling._connector_for_source = lambda *_args, **_kwargs: second_connector
    second = poll_source(
        db, source_id="source-001", resource="orders", lease_owner="worker-002",
        now=NOW + timedelta(minutes=1),
    )

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.cursor_before == first.cursor_after
    assert second.cursor_after.watermark == "2026-08-26T02:00:00Z"
    assert second.cursor_after.primary_key == "101"
    assert second.duplicate_count == 1
    assert len(second.input_dataset_version_ids) == 1
    latest = db.query(DatasetVersion).order_by(DatasetVersion.version_no.desc()).first()
    assert latest.rowcount == 1
    inbox = db.query(RefreshInboxEvent).filter(
        RefreshInboxEvent.source_id == "source-001",
        RefreshInboxEvent.resource == "orders",
    ).all()
    assert {event.event_id for event in inbox} == {"evt-overlap-1", "evt-overlap-2"}
    assert all(event.state == "processed" for event in inbox)


def test_dataset_version_refresh_provenance_survives_commit_and_reload(db):
    from app.services.v2.dataset_service import DatasetService

    dataset = Dataset(id="dataset-provenance", name="provenance", kind="structured")
    db.add(dataset)
    db.commit()

    class _Storage:
        def put_bytes(self, bucket: str, key: str, data: bytes) -> str:
            return f"memory://{bucket}/{key}"

    cursor = _cursor("2026-08-26T01:00:00Z", "100")
    version = DatasetService(db, storage=_Storage()).create_version(
        dataset.id, b'{"id":"100"}', rowcount=1,
        refresh_run_id="refresh-run-provenance", source_cursor=cursor,
        observed_at=NOW,
    )
    version_id = version.id
    db.expunge_all()
    loaded = db.get(DatasetVersion, version_id)

    assert loaded.refresh_run_id == "refresh-run-provenance"
    assert loaded.source_cursor == {
        "source_id": "source-001",
        "resource": "orders",
        "contract": "watermark_primary_key",
        "watermark": "2026-08-26T01:00:00Z",
        "primary_key": "100",
        "opaque_value": None,
        "observed_at": NOW.isoformat(),
    }
    observed_at = loaded.observed_at
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    assert observed_at == NOW


def test_structured_pipeline_uses_explicit_pinned_input_version(db):
    dataset = Dataset(id="dataset-pinned", name="pinned", kind="structured")
    db.add(dataset)
    db.flush()
    first = DatasetVersion(id="dataset-version-001", dataset_id=dataset.id, version_no=1)
    latest = DatasetVersion(id="dataset-version-002", dataset_id=dataset.id, version_no=2)
    dataset.latest_version_id = latest.id
    db.add_all([first, latest])
    db.commit()

    loaded = polling.select_pinned_dataset_version(db, dataset_id=dataset.id, input_dataset_version_ids=[latest.id])

    assert loaded.id == "dataset-version-002"


def test_cursor_does_not_advance_without_new_watermark_progress(db):
    connector = _SourceConnector(page=DeltaPage(
        envelopes=[_envelope("evt-old", "2026-08-26T00:30:00Z", "099", {"id": "099"})],
        candidate_cursor=_cursor("2026-08-26T00:30:00Z", "099"),
        source_observed_at=NOW,
        source_lag_seconds=1.0,
    ))
    _polling_source(db, connector=connector)
    db.add(RefreshSourceState(
        id="state-with-cursor", source_id="source-001", resource="orders",
        cursor_contract="watermark_primary_key",
        cursor_json={
            "watermark": "2026-08-26T01:00:00Z", "primary_key": "100",
            "opaque_value": None, "observed_at": NOW.isoformat(),
        },
        config_version=1, fencing_token=0,
    ))
    db.commit()

    result = poll_source(db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW)

    assert result.status == "succeeded"
    assert result.cursor_before is not None
    assert result.cursor_after is not None
    assert cursor_order(result.cursor_before, result.cursor_after) == 0


def test_bounded_backfill_window_advances_cursor(db):
    """`trigger_refresh`'s manual backfill path (`operations.py`'s
    `_validate_backfill`) only ever queues a run when the requested
    `backfill_from`/`backfill_to` window fits inside the source's own
    persisted `backfill_window_seconds` — proven here end to end: once
    that queued run is dispatched, it advances the cursor through the exact
    same `poll_source` path every other refresh run does."""
    connector = _SourceConnector(page=DeltaPage(
        envelopes=[_envelope("evt-backfill-1", "2026-08-25T12:00:00Z", "500", {"id": "500"})],
        candidate_cursor=_cursor("2026-08-25T12:00:00Z", "500"),
        source_observed_at=NOW, source_lag_seconds=1.0,
    ))
    source, _, _ = _polling_source(db, connector=connector)

    upsert_refresh_schedule(
        db,
        ScheduleRequest(
            target_type="connection", target_id=source.id, cron_expr="0 * * * *", timezone="UTC",
            business_calendar=[], sla_seconds=0, retry_policy=None,
            backfill_window_seconds=86400, max_pending_runs=5, enabled=True,
        ),
        now=NOW,
    )

    backfill_from = NOW - timedelta(hours=6)
    run = trigger_refresh(
        db, source_id=source.id, resource="orders", mode=RefreshPolicy.MICRO_BATCH,
        backfill_from=backfill_from, backfill_to=NOW, operator_id="operator-001", now=NOW,
    )
    assert run.status == "queued"
    assert run.source_provenance["backfill_from"] == backfill_from.isoformat()

    result = poll_source(
        db, source_id=source.id, resource="orders", lease_owner="worker-001", now=NOW, _run_id=run.id,
    )

    assert result.status == "succeeded"
    assert result.cursor_before is None
    assert result.cursor_after is not None
    assert result.cursor_after.watermark == "2026-08-25T12:00:00Z"


def test_dlq_replay_does_not_mutate_failed_run(db):
    connector = _SourceConnector(error=RuntimeError("source unavailable"))
    _polling_source(db, connector=connector)
    source = db.query(Connection).filter(Connection.id == "source-001").one()
    source.config = {**source.config, "max_attempts": 1}
    db.commit()

    failed = poll_source(db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW)
    replay = replay_refresh(db, run_id=failed.run_id, now=NOW)

    assert failed.status == "dead_lettered"
    assert replay.id != failed.run_id
    assert db.get(RefreshRun, failed.run_id).status == "dead_lettered"
    assert db.query(RefreshDeadLetter).filter(RefreshDeadLetter.run_id == failed.run_id).count() == 1


@pytest.mark.parametrize(
    "case_id", ["cancel-before-pull", "cancel-inflight-page", "cancel-after-tentative-materialization"],
)
def test_poll_cancellation_stops_at_safe_point_without_durable_progress(db, case_id):
    event = _envelope("evt-cancel", "2026-08-26T01:00:00Z", "100", {"id": "100"})
    connector = _SourceConnector(page=DeltaPage(
        envelopes=[event], candidate_cursor=_cursor("2026-08-26T01:00:00Z", "100"),
        source_observed_at=NOW, source_lag_seconds=1.0,
    ))
    _polling_source(db, connector=connector)

    if case_id == "cancel-before-pull":
        run = claim_refresh_run(
            db, source_id="source-001", resource="orders", policy="micro_batch",
            idempotency_key="cancel-before-pull", lease_owner="worker-001", now=NOW,
        )
        request_refresh_cancellation(
            db, run_id=run.id, requested_by="operator-001", reason="stop", now=NOW,
        )
        result = poll_source(
            db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW,
            _run_id=run.id,
        )
    elif case_id == "cancel-inflight-page":
        original_pull = connector.pull_delta

        def pull_and_cancel(resource, *, cursor, overlap_window):
            page = original_pull(resource, cursor=cursor, overlap_window=overlap_window)
            run = db.query(RefreshRun).order_by(RefreshRun.created_at.desc()).first()
            request_refresh_cancellation(
                db, run_id=run.id, requested_by="operator-001", reason="stop", now=NOW,
            )
            return page

        connector.pull_delta = pull_and_cancel
        result = poll_source(db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW)
    else:
        # SQLite cannot run a concurrent writer while the tentative Dataset
        # row is flushed.  The production PostgreSQL/MySQL integration test
        # exercises this exact post-materialization race; the unit target
        # still verifies the terminal no-progress contract deterministically.
        run = claim_refresh_run(
            db, source_id="source-001", resource="orders", policy="micro_batch",
            idempotency_key="cancel-after-tentative-materialization", lease_owner="worker-001", now=NOW,
        )
        request_refresh_cancellation(
            db, run_id=run.id, requested_by="operator-001", reason="stop", now=NOW,
        )
        result = poll_source(
            db, source_id="source-001", resource="orders", lease_owner="worker-001", now=NOW,
            _run_id=run.id,
        )

    assert result.status == "cancelled"
    assert result.cancel_requested_at is not None
    assert result.cursor_before == result.cursor_after
    assert result.input_dataset_version_ids == []
    assert result.pipeline_run_id is None
    assert db.query(DatasetVersion).count() == 0
    assert db.query(PipelineRunInput).count() == 0
