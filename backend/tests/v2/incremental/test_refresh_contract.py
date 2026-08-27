"""Durable source refresh contract tests (Task 6).

`db` (SQLite, function-scoped, autoused table creation) comes from the
top-level tests/conftest.py and is used for tests that don't need real
row-level locking. `concurrent_refresh_db` (defined below) is backed by a
real, disposable PostgreSQL schema migrated to head — it's a genuine
Session against a real database (not a mock), used directly by the
production contract functions exactly like `db`, and additionally exposes
`.new_session()`/`.barrier()` so a test can drive two truly independent
sessions/threads through a synchronization barrier. The two literal fencing
races below only need one session each (their correctness comes from real
committed Postgres state, not from thread timing); the additional
`test_concurrent_claims_for_a_fresh_source_yield_exactly_one_winner` test
proves the same `claim_refresh_run` path is safe under genuine concurrent
access from two real threads/connections.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineRunInput
from app.models.v2.refresh import RefreshInboxEvent, RefreshRun, RefreshSourceState
from app.schemas.refresh import (
    ChangeEnvelope,
    ConfigurationDriftError,
    RefreshCancellationRequested,
    RefreshError,
    RefreshFencingError,
    RefreshLeaseError,
    RefreshPolicy,
    SourceCursor,
    cursor_order,
    dedupe_key,
    normalize_change_envelope,
)
from app.services.v2.incremental.contract import (
    assert_refresh_not_cancelled,
    claim_refresh_run,
    finalize_refresh_cancellation,
    mark_refresh_retryable,
    record_refresh_outcome,
    request_refresh_cancellation,
    update_source_configuration,
)

FIXED_NOW = datetime(2026, 8, 26, 0, 0, 0, tzinfo=timezone.utc)

BACKEND_DIR = Path(__file__).resolve().parents[3]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


# ── concurrent_refresh_db: real PostgreSQL, per-test disposable schema ────

def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _migrate(schema: str) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_migrations.py", "upgrade", "head"],
        cwd=BACKEND_DIR, env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"migration failed:\n{result.stdout}\n{result.stderr}"


class ConcurrentRefreshDB:
    """A real Session (used directly by contract functions, like `db`) that
    also exposes genuinely independent sessions/threads for true concurrency
    tests. Dialect-parameterizable by design (only `TEST_DATABASE_URL`
    determines the dialect) so a later MySQL-parameterized cancellation test
    can reuse this exact fixture pattern without rework.
    """

    def __init__(self, engine, schema: str):
        self._engine = engine
        self.schema = schema
        self.session = sessionmaker(bind=engine)()

    def new_session(self):
        """An independent Session on its own connection, for a genuine
        multi-thread/multi-worker race against the same schema."""
        return sessionmaker(bind=self._engine)()

    @staticmethod
    def barrier(n: int = 2) -> threading.Barrier:
        return threading.Barrier(n)

    def __getattr__(self, name):
        # Duck-type as the default Session so production functions
        # (`db: Session`) can take this fixture directly.
        return getattr(self.session, name)


@pytest.fixture
def concurrent_refresh_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "refresh_" + uuid.uuid4().hex

    # Every engine here is short-lived and explicitly disposed in a
    # try/finally — including the admin engine's own setup step — so a
    # failure partway through setup (e.g. schema creation) can never leak a
    # pooled connection into the rest of the suite.
    admin_engine = create_engine(TEST_DATABASE_URL)
    try:
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    finally:
        admin_engine.dispose()

    _migrate(schema)

    engine = create_engine(_scoped_url(schema))
    wrapper = ConcurrentRefreshDB(engine, schema)
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


# ── fixture helpers ─────────────────────────────────────────────────────

DEFAULT_SOURCE = "source-001"
DEFAULT_RESOURCE = "orders"


def cursor_fixture(watermark, primary_key, *, source_id=DEFAULT_SOURCE, resource=DEFAULT_RESOURCE) -> SourceCursor:
    return SourceCursor(
        source_id=source_id, resource=resource, contract="watermark_primary_key",
        watermark=watermark, primary_key=primary_key, opaque_value=None, observed_at=FIXED_NOW,
    )


def persist_fixture_event(db, *, event_id, source_id=DEFAULT_SOURCE, resource=DEFAULT_RESOURCE) -> RefreshInboxEvent:
    existing = db.execute(
        select(RefreshInboxEvent).where(
            RefreshInboxEvent.source_id == source_id,
            RefreshInboxEvent.resource == resource,
            RefreshInboxEvent.event_id == event_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.state = "duplicate"
        db.commit()
        db.refresh(existing)
        return existing
    row = RefreshInboxEvent(id=str(uuid.uuid4()), source_id=source_id, resource=resource, event_id=event_id, state="received")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def count_fixture_inbox_events(db, event_id) -> int:
    return db.execute(
        select(func.count()).select_from(RefreshInboxEvent).where(RefreshInboxEvent.event_id == event_id)
    ).scalar_one()


def persist_fixture_source_state(db, *, source_id, resource, config_version=1,
                                  cursor_contract="watermark_primary_key") -> RefreshSourceState:
    row = RefreshSourceState(
        id=str(uuid.uuid4()), source_id=source_id, resource=resource,
        cursor_contract=cursor_contract, config_version=config_version, fencing_token=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def configure_fixture_source(db, *, source_id, resource, cursor_contract, config_version) -> RefreshSourceState:
    return persist_fixture_source_state(
        db, source_id=source_id, resource=resource, config_version=config_version, cursor_contract=cursor_contract,
    )


def claim_fixture_run(db, *, idempotency_key, lease_owner, source_id=DEFAULT_SOURCE, resource=DEFAULT_RESOURCE,
                       policy=RefreshPolicy.BATCH, now=FIXED_NOW, lease_seconds=300) -> RefreshRun:
    return claim_refresh_run(
        db, source_id=source_id, resource=resource, policy=policy,
        idempotency_key=idempotency_key, lease_owner=lease_owner, now=now, lease_seconds=lease_seconds,
    )


def read_source_state(db, source_id=DEFAULT_SOURCE, resource=DEFAULT_RESOURCE) -> RefreshSourceState:
    return db.execute(
        select(RefreshSourceState).where(RefreshSourceState.source_id == source_id, RefreshSourceState.resource == resource)
    ).scalar_one()


def read_fixture_cursor(db, source_id=DEFAULT_SOURCE, resource=DEFAULT_RESOURCE):
    return read_source_state(db, source_id, resource).cursor


def get_refresh_run(db, run_id) -> RefreshRun:
    return db.get(RefreshRun, run_id)


def list_pipeline_inputs(db, pipeline_run_id) -> list[str]:
    rows = db.execute(
        select(PipelineRunInput.dataset_version_id)
        .where(PipelineRunInput.pipeline_run_id == pipeline_run_id)
        .order_by(PipelineRunInput.input_ordinal)
    ).scalars().all()
    return list(rows)


def dataset_version_exists(db, dataset_version_id) -> bool:
    return db.execute(
        select(PipelineRunInput.id).where(PipelineRunInput.dataset_version_id == dataset_version_id)
    ).first() is not None


def pipeline_run_exists(db, pipeline_run_id) -> bool:
    return db.execute(
        select(RefreshRun.id).where(RefreshRun.pipeline_run_id == pipeline_run_id)
    ).first() is not None


def _ensure_fixture_pipeline(db) -> Pipeline:
    pipeline = db.execute(select(Pipeline).where(Pipeline.name == "refresh-contract-fixture-pipeline")).scalar_one_or_none()
    if pipeline is not None:
        return pipeline
    pipeline = Pipeline(id=str(uuid.uuid4()), name="refresh-contract-fixture-pipeline", spec={})
    db.add(pipeline)
    db.flush()
    return pipeline


def _ensure_fixture_dataset_version(db, dataset_version_id) -> DatasetVersion:
    existing = db.get(DatasetVersion, dataset_version_id)
    if existing is not None:
        return existing
    dataset = db.execute(select(Dataset).where(Dataset.name == "refresh-contract-fixture-dataset")).scalar_one_or_none()
    if dataset is None:
        dataset = Dataset(id=str(uuid.uuid4()), name="refresh-contract-fixture-dataset", kind="structured")
        db.add(dataset)
        db.flush()
    dataset_version = DatasetVersion(id=dataset_version_id, dataset_id=dataset.id, version_no=1)
    db.add(dataset_version)
    db.flush()
    return dataset_version


def persist_fixture_pipeline_run(db, *, input_dataset_version_ids) -> PipelineRun:
    pipeline = _ensure_fixture_pipeline(db)
    dataset_versions = [
        _ensure_fixture_dataset_version(db, dataset_version_id)
        for dataset_version_id in input_dataset_version_ids
    ]
    is_completed = bool(dataset_versions)
    run = PipelineRun(
        id=str(uuid.uuid4()),
        pipeline_id=pipeline.id,
        status="success" if is_completed else "running",
        finished_at=FIXED_NOW if is_completed else None,
        dataset_version_id=dataset_versions[0].id if dataset_versions else None,
    )
    db.add(run)
    db.flush()
    for ordinal, dataset_version in enumerate(dataset_versions):
        db.add(PipelineRunInput(
            id=str(uuid.uuid4()), pipeline_run_id=run.id, dataset_version_id=dataset_version.id, input_ordinal=ordinal,
        ))
    db.commit()
    db.refresh(run)
    return run


def fixture_run_with_cursor(db, *, watermark, primary_key, source_id=DEFAULT_SOURCE, resource=DEFAULT_RESOURCE) -> RefreshRun:
    cursor_json = {"watermark": watermark, "primary_key": primary_key, "opaque_value": None, "observed_at": FIXED_NOW.isoformat()}
    state = RefreshSourceState(
        id=str(uuid.uuid4()), source_id=source_id, resource=resource,
        cursor_contract="watermark_primary_key", config_version=1, fencing_token=1, cursor_json=cursor_json,
    )
    db.add(state)
    run = RefreshRun(
        id=str(uuid.uuid4()), source_id=source_id, resource=resource,
        policy=RefreshPolicy.BATCH.value, config_version=1, cursor_contract="watermark_primary_key",
        status="running", dispatch_state="pending", idempotency_key="fixture-run-with-cursor",
        fencing_token=1, retry_count=0, cursor_before_json=cursor_json,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def mark_fixture_failed(db, run_id) -> None:
    run = db.get(RefreshRun, run_id)
    run.status = "failed"
    run.terminal_at = FIXED_NOW
    db.commit()


def mark_fixture_terminal(db, run_id: str, *, status: Literal["succeeded", "failed", "dead_lettered", "cancelled"]) -> None:
    """Commit a terminal state without creating a DatasetVersion, PipelineRun,
    or lineage association — except for "succeeded", which uses the normal
    record_refresh_outcome path (so lineage IS created for that one case)."""
    if status == "succeeded":
        run = db.get(RefreshRun, run_id)
        record_refresh_outcome(
            db, run_id=run_id, lease_owner=run.lease_owner, fencing_token=run.fencing_token,
            config_version=run.config_version, cursor_contract=run.cursor_contract,
            input_dataset_version_ids=[f"dataset-version-{run_id}"], pipeline_run_id=f"pipeline-run-{run_id}",
            next_cursor=cursor_fixture("2026-08-26T09:00:00Z", "999", source_id=run.source_id, resource=run.resource),
            quality_summary={}, provenance={}, now=FIXED_NOW,
        )
        return
    run = db.get(RefreshRun, run_id)
    run.status = status
    run.terminal_at = FIXED_NOW
    db.commit()


# ── pure value tests ────────────────────────────────────────────────────

def test_watermark_cursor_orders_equal_timestamps_by_primary_key():
    older = SourceCursor(source_id="source-001", resource="orders", contract="watermark_primary_key", watermark="2026-08-26T01:00:00Z", primary_key="100", opaque_value=None, observed_at=FIXED_NOW)
    newer = SourceCursor(source_id="source-001", resource="orders", contract="watermark_primary_key", watermark="2026-08-26T01:00:00Z", primary_key="101", opaque_value=None, observed_at=FIXED_NOW)
    assert cursor_order(older, newer) == -1


def test_opaque_cursor_orders_by_opaque_value_only():
    left = SourceCursor(source_id="s", resource="r", contract="opaque_source_cursor", watermark=None, primary_key=None, opaque_value="a", observed_at=FIXED_NOW)
    right = SourceCursor(source_id="s", resource="r", contract="opaque_source_cursor", watermark=None, primary_key=None, opaque_value="b", observed_at=FIXED_NOW)
    assert cursor_order(left, right) == -1
    assert cursor_order(right, left) == 1
    assert cursor_order(left, left) == 0


def test_normalize_change_envelope_builds_envelope_from_valid_payload():
    envelope = normalize_change_envelope(
        {"event_id": "evt-100", "operation": "upsert", "primary_key": "100", "watermark": "2026-08-26T01:00:00Z", "payload": {"a": 1}},
        source_id="source-001", resource="orders", received_at=FIXED_NOW,
    )
    assert isinstance(envelope, ChangeEnvelope)
    assert envelope.event_id == "evt-100"
    assert envelope.source_id == "source-001"
    assert envelope.resource == "orders"


def test_normalize_change_envelope_rejects_missing_event_identity():
    with pytest.raises(RefreshError):
        normalize_change_envelope(
            {"operation": "upsert", "primary_key": "100", "watermark": "2026-08-26T01:00:00Z"},
            source_id="source-001", resource="orders", received_at=FIXED_NOW,
        )


def test_normalize_change_envelope_rejects_source_resource_mismatch():
    with pytest.raises(RefreshError):
        normalize_change_envelope(
            {"event_id": "evt-1", "source_id": "source-999", "operation": "upsert", "primary_key": "100", "watermark": "2026-08-26T01:00:00Z"},
            source_id="source-001", resource="orders", received_at=FIXED_NOW,
        )


def test_dedupe_key_is_stable_and_differs_by_payload():
    base = dict(event_id="evt-1", source_id="source-001", resource="orders", operation="upsert",
                primary_key="100", watermark="2026-08-26T01:00:00Z", source_cursor=None, schema_hash="h1",
                occurred_at=FIXED_NOW, received_at=FIXED_NOW)
    first = ChangeEnvelope(**base | {"payload": {"a": 1}})
    same_again = ChangeEnvelope(**base | {"payload": {"a": 1}})
    different = ChangeEnvelope(**base | {"payload": {"a": 2}})
    assert dedupe_key(first) == dedupe_key(same_again)
    assert dedupe_key(first) != dedupe_key(different)


# ── durable inbox identity (SQLite `db`) ───────────────────────────────

def test_duplicate_event_has_one_durable_inbox_identity(db):
    first = persist_fixture_event(db, event_id="evt-001")
    second = persist_fixture_event(db, event_id="evt-001")
    assert first.id == second.id
    assert count_fixture_inbox_events(db, "evt-001") == 1


# ── claim/fence/CAS races (real PostgreSQL) ────────────────────────────

def test_second_worker_claim_is_rejected_while_first_lease_is_active(concurrent_refresh_db):
    first = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-001", lease_owner="worker-a")
    assert first.fencing_token == read_source_state(concurrent_refresh_db).fencing_token
    same = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-001", lease_owner="worker-a")
    assert same.id == first.id
    assert same.fencing_token == first.fencing_token
    with pytest.raises(RefreshLeaseError):
        claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-002", lease_owner="worker-b")


def test_expired_worker_late_finish_cannot_overwrite_newer_cursor(concurrent_refresh_db):
    old = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-old", lease_owner="worker-a", now=FIXED_NOW, lease_seconds=60)
    new_now = FIXED_NOW + timedelta(minutes=10)
    new = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-new", lease_owner="worker-b", now=new_now)
    record_refresh_outcome(concurrent_refresh_db, run_id=new.id, lease_owner="worker-b", fencing_token=new.fencing_token, config_version=new.config_version, cursor_contract=new.cursor_contract, input_dataset_version_ids=["dataset-version-new"], pipeline_run_id="pipeline-run-new", next_cursor=cursor_fixture("2026-08-26T02:00:00Z", "200"), quality_summary={}, provenance={}, now=new_now)
    with pytest.raises(RefreshFencingError):
        record_refresh_outcome(concurrent_refresh_db, run_id=old.id, lease_owner="worker-a", fencing_token=old.fencing_token, config_version=old.config_version, cursor_contract=old.cursor_contract, input_dataset_version_ids=["dataset-version-old"], pipeline_run_id="pipeline-run-old", next_cursor=cursor_fixture("2026-08-26T01:00:00Z", "199"), quality_summary={}, provenance={}, now=new_now)
    assert read_fixture_cursor(concurrent_refresh_db).primary_key == "200"
    assert list_pipeline_inputs(concurrent_refresh_db, "pipeline-run-old") == []


def test_configuration_upgrade_returns_configuration_drift_before_fence_check_without_lineage_or_progress(concurrent_refresh_db):
    configure_fixture_source(concurrent_refresh_db, source_id="source-001", resource="orders", cursor_contract="watermark_primary_key", config_version=7)
    old = claim_fixture_run(concurrent_refresh_db, source_id="source-001", resource="orders", idempotency_key="refresh-config-old", lease_owner="worker-a", policy=RefreshPolicy.MICRO_BATCH, lease_seconds=600)
    before_cursor = read_fixture_cursor(concurrent_refresh_db)
    upgraded = update_source_configuration(concurrent_refresh_db, source_id="source-001", resource="orders", cursor_contract="watermark_primary_key", configuration={"schema_hash": "schema-v2"}, now=FIXED_NOW + timedelta(minutes=1))
    assert upgraded.config_version == old.config_version + 1
    assert upgraded.lease_owner is None
    assert upgraded.fencing_token > old.fencing_token
    # The old run has an invalidated lease/fence, but the frozen revision/contract mismatch is checked first.
    with pytest.raises(ConfigurationDriftError) as exc:
        record_refresh_outcome(concurrent_refresh_db, run_id=old.id, lease_owner="worker-a", fencing_token=old.fencing_token, config_version=old.config_version, cursor_contract=old.cursor_contract, input_dataset_version_ids=["dataset-version-config-old"], pipeline_run_id="pipeline-run-config-old", next_cursor=cursor_fixture("2026-08-26T02:00:00Z", "200"), quality_summary={}, provenance={}, now=FIXED_NOW + timedelta(minutes=2))
    assert exc.value.reason_code == "CONFIGURATION_DRIFT"
    assert dataset_version_exists(concurrent_refresh_db, "dataset-version-config-old") is False
    assert pipeline_run_exists(concurrent_refresh_db, "pipeline-run-config-old") is False
    assert list_pipeline_inputs(concurrent_refresh_db, "pipeline-run-config-old") == []
    assert read_fixture_cursor(concurrent_refresh_db) == before_cursor


def test_source_state_is_unique(concurrent_refresh_db):
    persist_fixture_source_state(concurrent_refresh_db, source_id="source-001", resource="orders")
    with pytest.raises(IntegrityError):
        persist_fixture_source_state(concurrent_refresh_db, source_id="source-001", resource="orders")


def test_pipeline_run_records_all_input_versions(db):
    run = persist_fixture_pipeline_run(db, input_dataset_version_ids=["dataset-version-001", "dataset-version-002"])
    assert list_pipeline_inputs(db, run.id) == ["dataset-version-001", "dataset-version-002"]


def test_failed_outcome_does_not_advance_source_cursor(db):
    run = fixture_run_with_cursor(db, watermark="2026-08-26T01:00:00Z", primary_key="100")
    mark_fixture_failed(db, run.id)
    assert read_fixture_cursor(db).primary_key == "100"


def test_mark_refresh_retryable_resets_lease_and_increments_retry_count(db):
    run = fixture_run_with_cursor(db, watermark="2026-08-26T01:00:00Z", primary_key="100")
    run.lease_owner = "worker-a"
    run.lease_expires_at = FIXED_NOW
    db.commit()
    retried = mark_refresh_retryable(db, run_id=run.id, reason="worker lost", now=FIXED_NOW + timedelta(minutes=1))
    assert retried.status == "queued"
    assert retried.retry_count == 1
    assert retried.lease_owner is None
    assert retried.dispatch_state == "pending"
    assert read_fixture_cursor(db).primary_key == "100"


def test_mark_refresh_retryable_is_a_noop_for_a_terminal_run(db):
    run = fixture_run_with_cursor(db, watermark="2026-08-26T01:00:00Z", primary_key="100")
    mark_fixture_failed(db, run.id)
    unchanged = mark_refresh_retryable(db, run_id=run.id, reason="ignored", now=FIXED_NOW)
    assert unchanged.status == "failed"
    assert unchanged.retry_count == 0


# ── cancellation state machine (real PostgreSQL) ───────────────────────

def test_fenced_cancellation_request_records_actor_reason_and_safe_point_state(concurrent_refresh_db):
    run = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-cancel-001", lease_owner="worker-a")
    requested = request_refresh_cancellation(concurrent_refresh_db, run_id=run.id, requested_by="operator-001", reason="source maintenance", now=FIXED_NOW + timedelta(seconds=1))
    assert requested.status == "cancel_requested"
    assert requested.cancel_requested_by == "operator-001"
    assert requested.cancel_reason == "source maintenance"
    assert requested.cancel_fencing_token == run.fencing_token
    cancelled = finalize_refresh_cancellation(concurrent_refresh_db, run_id=run.id, lease_owner="worker-a", fencing_token=run.fencing_token, now=FIXED_NOW + timedelta(seconds=2))
    assert cancelled.status == "cancelled"
    assert read_fixture_cursor(concurrent_refresh_db) == run.cursor_before
    assert list_pipeline_inputs(concurrent_refresh_db, run.id) == []


def test_cancellation_request_is_idempotent_and_stale_worker_cannot_finalize(concurrent_refresh_db):
    run = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-cancel-002", lease_owner="worker-a")
    requested = request_refresh_cancellation(concurrent_refresh_db, run_id=run.id, requested_by="operator-001", reason="stop", now=FIXED_NOW)
    assert request_refresh_cancellation(concurrent_refresh_db, run_id=run.id, requested_by="operator-001", reason="stop", now=FIXED_NOW) == requested
    with pytest.raises(RefreshFencingError):
        finalize_refresh_cancellation(concurrent_refresh_db, run_id=run.id, lease_owner="worker-a", fencing_token=run.fencing_token - 1, now=FIXED_NOW)


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "dead_lettered", "cancelled"])
def test_cancellation_after_any_terminal_state_is_a_plain_already_terminal_result(concurrent_refresh_db, terminal_status):
    run = claim_fixture_run(concurrent_refresh_db, idempotency_key=f"refresh-cancel-{terminal_status}", lease_owner="worker-a")
    mark_fixture_terminal(concurrent_refresh_db, run.id, status=terminal_status)
    before_cursor = read_fixture_cursor(concurrent_refresh_db)
    before_inputs = list_pipeline_inputs(concurrent_refresh_db, run.id)
    result = request_refresh_cancellation(concurrent_refresh_db, run_id=run.id, requested_by="operator-001", reason="too late", now=FIXED_NOW + timedelta(seconds=1))
    assert result.already_terminal is True
    assert result.status == terminal_status
    unchanged = get_refresh_run(concurrent_refresh_db, run.id)
    assert unchanged.status == terminal_status
    if terminal_status != "succeeded":
        assert read_fixture_cursor(concurrent_refresh_db) == before_cursor
        assert list_pipeline_inputs(concurrent_refresh_db, run.id) == before_inputs


def test_assert_refresh_not_cancelled_raises_once_cancellation_is_requested(concurrent_refresh_db):
    run = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-cancel-guard", lease_owner="worker-a")
    assert_refresh_not_cancelled(concurrent_refresh_db, run_id=run.id, lease_owner="worker-a", fencing_token=run.fencing_token, now=FIXED_NOW)
    request_refresh_cancellation(concurrent_refresh_db, run_id=run.id, requested_by="operator-001", reason="stop", now=FIXED_NOW)
    with pytest.raises(RefreshCancellationRequested):
        assert_refresh_not_cancelled(concurrent_refresh_db, run_id=run.id, lease_owner="worker-a", fencing_token=run.fencing_token, now=FIXED_NOW)


# ── genuine multi-thread/multi-session concurrency ─────────────────────

def test_concurrent_claims_for_a_fresh_source_yield_exactly_one_winner(concurrent_refresh_db):
    """Two real threads, each on its own Session/connection, race
    claim_refresh_run for a brand-new (source_id, resource) at the same
    instant (a threading.Barrier holds them at the starting line). Exactly
    one must win the lease; the other must observe it as already active."""
    barrier = concurrent_refresh_db.barrier(2)
    results: dict[str, object] = {}
    errors: dict[str, Exception] = {}
    lock = threading.Lock()

    def attempt(owner: str) -> None:
        session = concurrent_refresh_db.new_session()
        try:
            barrier.wait(timeout=10)
            run = claim_refresh_run(
                session, source_id="source-race", resource="orders", policy=RefreshPolicy.BATCH,
                idempotency_key=f"refresh-race-{owner}", lease_owner=owner, now=FIXED_NOW, lease_seconds=300,
            )
            with lock:
                results[owner] = run
        except RefreshLeaseError as exc:
            with lock:
                errors[owner] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=attempt, args=(owner,)) for owner in ("racer-a", "racer-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert len(results) == 1, f"expected exactly one winner, got {results!r} / {errors!r}"
    assert len(errors) == 1
    assert isinstance(next(iter(errors.values())), RefreshLeaseError)
