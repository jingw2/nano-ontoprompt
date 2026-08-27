"""Task 8 cancellation safe-point checks against both SQL dialect fixtures."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from app.models.v2.connection import Connection
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.pipeline import Pipeline
from app.models.v2.refresh import RefreshRun
from app.services.connection.sql_connector import SQLConnector
from app.services.v2.incremental import polling
from app.services.v2.incremental.contract import request_refresh_cancellation as cancel_run
from app.services.v2.incremental.polling import poll_source


NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)


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
