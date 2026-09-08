"""Task 8/9 cancellation safe-point checks against both SQL dialect fixtures.

The polling-path tests below drive the connector against a real dialect
database while the app's own RefreshRun/RefreshSourceState state lives in
the SQLite `db` fixture (only the connector's SQL generation is
dialect-sensitive there). The event-path tests added for Task 9 instead need
the app's *own* RefreshRun/RefreshInboxEvent tables backed by a real
Postgres/MySQL connection, because `accept()`/`process()` never call an
external connector at all -- the genuinely dialect-sensitive behavior here is
row-level locking (`SELECT ... FOR UPDATE`) at the cancellation safe points,
which SQLite cannot exercise faithfully (see the "cancel-after-tentative-
materialization" comment in `test_refresh_polling.py`). Those tests reuse
`test_event_ingest_integration.py`'s migrated-schema/database bootstrap.
"""
from __future__ import annotations

import contextlib
import os
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.v2.connection import Connection
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.pipeline import Pipeline, PipelineRun
from app.models.v2.refresh import RefreshInboxEvent, RefreshRun
from app.services.connection.sql_connector import SQLConnector
from app.services.v2.incremental import event_ingest, polling
from app.services.v2.incremental.contract import request_refresh_cancellation as cancel_run
from app.services.v2.incremental.event_adapters import ManagedOutboxAdapter
from app.services.v2.incremental.event_ingest import EventIngestService
from app.services.v2.incremental.polling import poll_source

from .test_event_ingest_integration import (
    _migrate,
    _mysql_admin_url,
    _pre_refresh_tables,
    _scoped_postgres_url,
)


NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)


def _event_record(event_id: str, *, watermark: str = "2026-08-26T01:00:00Z") -> dict[str, object]:
    return {
        "version": 1,
        "event_id": event_id,
        "source_id": "task9-cancel-source",
        "resource": "orders",
        "operation": "upsert",
        "primary_key": event_id,
        "payload": {"id": event_id, "state": "ready"},
        "watermark": watermark,
        "schema_hash": "schema-v1",
        "occurred_at": NOW.isoformat(),
    }


@contextlib.contextmanager
def _migrated_dialect_engine(dialect: str, source_url: str):
    """Bootstrap a disposable, migrated app database on a real dialect
    fixture, mirroring `test_event_ingest_integration.py`'s per-test
    schema/database. Yields a bound SQLAlchemy engine; the caller opens as
    many independent sessions against it as it needs (real concurrency, not
    a single shared session)."""
    import uuid
    from sqlalchemy.engine import make_url

    source = make_url(source_url)
    resource_name = f"task9_cancel_{dialect}_{uuid.uuid4().hex}"
    admin_engine = None
    cleanup_kind: str
    if dialect == "postgres":
        admin_engine = create_engine(source_url)
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{resource_name}"'))
        app_url = _scoped_postgres_url(source_url, resource_name)
        cleanup_kind = "schema"
    else:
        admin_url = _mysql_admin_url(source)
        admin_engine = create_engine(admin_url)
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{resource_name}`"))
        app_url = admin_url.set(database=resource_name).render_as_string(hide_password=False)
        cleanup_kind = "database"

    app_engine = create_engine(app_url)
    try:
        _pre_refresh_tables().create_all(app_engine)
        with app_engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
                {"revision": "0021_mapping_entity_class_cn"},
            )
        _migrate(app_url)
        yield app_engine
    finally:
        app_engine.dispose()
        if admin_engine is not None:
            try:
                with admin_engine.begin() as connection:
                    if cleanup_kind == "schema":
                        connection.execute(text(f'DROP SCHEMA IF EXISTS "{resource_name}" CASCADE'))
                    else:
                        connection.execute(text(f"DROP DATABASE IF EXISTS `{resource_name}`"))
            finally:
                admin_engine.dispose()


@pytest.mark.parametrize(
    ("dialect", "env_name"),
    [("postgres", "RUNTIME_POSTGRES_URL"), ("mysql", "RUNTIME_MYSQL_URL")],
)
def test_sql_poll_cancellation_at_page_boundary_keeps_app_cursor_unchanged(db, monkeypatch, dialect, env_name):
    url = os.environ.get(env_name)
    if not url:
        pytest.skip(f"{env_name} is not configured")
    if dialect == "postgres":
        pytest.importorskip("psycopg2")
    else:
        pytest.importorskip("pymysql")

    source_id = f"task8-{dialect}-source"
    source = Connection(
        id=source_id, name=f"{dialect} runtime source", kind="postgres" if dialect == "postgres" else "mysql",
        status="active", config={
            "connection_string": url,
            "cursor_contract": "watermark_primary_key",
            "watermark_column": "watermark",
            "primary_key_column": "row_id",
            "query": (
                "SELECT * FROM refresh_source_rows "
                "WHERE source_id = 'source-acme-erp' AND resource = 'purchase_orders'"
            ),
        },
    )
    dataset = Dataset(id=f"{dialect}-orders-dataset", name="refresh_source_rows", source_connection_id=source_id, kind="structured")
    pipeline = Pipeline(id=f"{dialect}-orders-pipeline", name=f"{dialect} pipeline", source_dataset_id=dataset.id, spec={}, status="active")
    db.add_all([source, dataset, pipeline])
    db.commit()

    real_connector = SQLConnector(dict(source.config))

    class CancelAtPage:
        def pull_delta(self, resource, *, cursor, overlap_window):
            page = real_connector.pull_delta(resource, cursor=cursor, overlap_window=overlap_window)
            run = db.query(RefreshRun).order_by(RefreshRun.created_at.desc()).first()
            cancel_run(db, run_id=run.id, requested_by="operator-001", reason="stop", now=NOW)
            return page

    monkeypatch.setattr(
        polling,
        "_connector_for_source",
        lambda *_args, **_kwargs: CancelAtPage(),
    )
    result = poll_source(
        db, source_id=source_id, resource="refresh_source_rows", lease_owner="worker-001", now=NOW,
    )

    assert result.status == "cancelled"
    assert result.cursor_before == result.cursor_after
    assert db.query(DatasetVersion).count() == 0


def _seed_event_source(session) -> None:
    session.add_all([
        Connection(
            id="task9-cancel-source", name="Task 9 cancellation source", kind="rest", status="active",
            config={"cursor_contract": "watermark_primary_key", "schema_hash": "schema-v1"},
        ),
        Dataset(id="task9-cancel-dataset", name="orders", source_connection_id="task9-cancel-source", kind="structured"),
        Pipeline(id="task9-cancel-pipeline", name="Task 9 cancellation pipeline", source_dataset_id="task9-cancel-dataset", spec={}, status="active"),
    ])
    session.commit()


@pytest.mark.parametrize(
    ("dialect", "env_name"),
    [("postgres", "RUNTIME_POSTGRES_URL"), ("mysql", "RUNTIME_MYSQL_URL")],
)
def test_event_cancellation_before_processing_keeps_inbox_and_cursor_unchanged(monkeypatch, dialect, env_name):
    """cancel-before-pull equivalent for the event path: cancellation is
    requested on the claimed run before `process()` does any work at all.
    `process()`'s own fast path (`if run.status == "cancel_requested":
    finalize`) must return `cancelled` without ever parsing the envelope,
    touching provenance, or materializing anything."""
    url = os.environ.get(env_name)
    if not url:
        pytest.skip(f"{env_name} is not configured")
    pytest.importorskip("psycopg2" if dialect == "postgres" else "pymysql")

    with _migrated_dialect_engine(dialect, url) as engine:
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            _seed_event_source(db)
            envelope = ManagedOutboxAdapter.normalize(
                _event_record(f"evt-{dialect}-before-pull"), source_id="task9-cancel-source", received_at=NOW,
            )
            service = EventIngestService()
            receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=NOW)
            assert receipt.status == "received"
            cancel_run(db, run_id=receipt.run_id, requested_by="operator-001", reason="stop", now=NOW)

            result = service.process(db, run_id=receipt.run_id, lease_owner="event-worker-001", now=NOW)

            assert result.status == "cancelled"
            assert result.cursor_before == result.cursor_after
            assert result.input_dataset_version_ids == []
            assert result.pipeline_run_id is None
            assert db.query(DatasetVersion).count() == 0
            assert db.query(RefreshInboxEvent).one().state == "received"
        finally:
            db.close()


@pytest.mark.parametrize(
    ("dialect", "env_name"),
    [("postgres", "RUNTIME_POSTGRES_URL"), ("mysql", "RUNTIME_MYSQL_URL")],
)
def test_event_cancellation_inflight_before_tentative_materialization_keeps_no_progress(monkeypatch, dialect, env_name):
    """cancel-inflight-page equivalent for the event path: a cancellation
    request lands after `process()` has already computed provenance/late
    counts for the accepted event (i.e. genuinely mid-flight) but before the
    tentative DatasetVersion/PipelineRun materialization. The re-check
    immediately before materialization must observe it and finalize
    `cancelled` with no lineage rows ever created."""
    url = os.environ.get(env_name)
    if not url:
        pytest.skip(f"{env_name} is not configured")
    pytest.importorskip("psycopg2" if dialect == "postgres" else "pymysql")

    with _migrated_dialect_engine(dialect, url) as engine:
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            _seed_event_source(db)
            envelope = ManagedOutboxAdapter.normalize(
                _event_record(f"evt-{dialect}-inflight"), source_id="task9-cancel-source", received_at=NOW,
            )
            service = EventIngestService()
            receipt = service.accept(db, envelope, lease_owner="event-worker-001", now=NOW)
            assert receipt.status == "received"

            call_count = {"n": 0}
            original_assert_config = polling._assert_configuration_current

            def cancel_on_second_safe_point(db_arg, run_arg):
                call_count["n"] += 1
                if call_count["n"] == 2:
                    # The run is genuinely mid-flight here: provenance/late
                    # counts were already computed and flushed by process()
                    # for this exact run before this second checkpoint.
                    cancel_run(db_arg, run_id=run_arg.id, requested_by="operator-001", reason="stop", now=NOW)
                return original_assert_config(db_arg, run_arg)

            monkeypatch.setattr(polling, "_assert_configuration_current", cancel_on_second_safe_point)

            result = service.process(db, run_id=receipt.run_id, lease_owner="event-worker-001", now=NOW)

            assert call_count["n"] == 2
            assert result.status == "cancelled"
            assert result.cursor_before == result.cursor_after
            assert result.input_dataset_version_ids == []
            assert result.pipeline_run_id is None
            assert db.query(DatasetVersion).count() == 0
            assert db.query(PipelineRun).count() == 0
            assert db.query(RefreshInboxEvent).one().state == "received"
        finally:
            db.close()


@pytest.mark.parametrize(
    ("dialect", "env_name"),
    [("postgres", "RUNTIME_POSTGRES_URL"), ("mysql", "RUNTIME_MYSQL_URL")],
)
def test_event_cancellation_after_tentative_materialization_rolls_back_before_commit(monkeypatch, dialect, env_name):
    """cancel-after-tentative-materialization equivalent for the event path:
    a genuinely concurrent cancellation request, committed from a second
    real session/connection, arrives after `process()` has flushed a
    tentative DatasetVersion/PipelineRun but before `record_refresh_outcome`
    commits the outcome. A single shared session cannot exercise this
    (committing the injected cancellation on the same session would also
    commit the tentative rows), so this uses two real sessions against the
    same migrated dialect database, synchronized with `threading.Event`."""
    url = os.environ.get(env_name)
    if not url:
        pytest.skip(f"{env_name} is not configured")
    pytest.importorskip("psycopg2" if dialect == "postgres" else "pymysql")

    with _migrated_dialect_engine(dialect, url) as engine:
        Session = sessionmaker(bind=engine)
        seed_db = Session()
        try:
            _seed_event_source(seed_db)
        finally:
            seed_db.close()

        envelope = ManagedOutboxAdapter.normalize(
            _event_record(f"evt-{dialect}-after-tentative"), source_id="task9-cancel-source", received_at=NOW,
        )
        service = EventIngestService()
        # `accept()` runs on its own session, matching production: the
        # webhook route accepts under a FastAPI request session while
        # `refresh.event` processes under a brand-new Celery task session
        # (`_refresh_event` calls `SessionLocal()` fresh per invocation).
        accept_db = Session()
        try:
            receipt = service.accept(accept_db, envelope, lease_owner="event-worker-001", now=NOW)
        finally:
            accept_db.close()
        assert receipt.status == "received"

        worker_db = Session()

        reached_pause = threading.Event()
        cancelled_committed = threading.Event()
        original_record_outcome = event_ingest.record_refresh_outcome

        def paused_record_outcome(*args, **kwargs):
            reached_pause.set()
            assert cancelled_committed.wait(timeout=10), "cancellation was never committed by the other session"
            return original_record_outcome(*args, **kwargs)

        monkeypatch.setattr(event_ingest, "record_refresh_outcome", paused_record_outcome)

        outcome: dict[str, object] = {}

        def run_process():
            outcome["result"] = service.process(
                worker_db, run_id=receipt.run_id, lease_owner="event-worker-001", now=NOW,
            )

        worker_thread = threading.Thread(target=run_process)
        worker_thread.start()
        try:
            assert reached_pause.wait(timeout=10), "process() never reached the tentative-materialization pause"
            cancel_db = Session()
            try:
                cancel_run(cancel_db, run_id=receipt.run_id, requested_by="operator-001", reason="stop", now=NOW)
            finally:
                cancel_db.close()
            cancelled_committed.set()
        finally:
            worker_thread.join(timeout=15)
            worker_db.close()

        result = outcome.get("result")
        assert result is not None, "process() did not return"
        assert result.status == "cancelled"
        assert result.cursor_before == result.cursor_after
        assert result.input_dataset_version_ids == []
        assert result.pipeline_run_id is None

        verify_db = Session()
        try:
            assert verify_db.query(DatasetVersion).count() == 0
            assert verify_db.query(PipelineRun).count() == 0
            assert verify_db.query(RefreshInboxEvent).one().state == "received"
            assert verify_db.get(RefreshRun, receipt.run_id).status == "cancelled"
        finally:
            verify_db.close()
