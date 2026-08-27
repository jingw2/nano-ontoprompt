"""Task 8 polling checks against the runtime PostgreSQL/MySQL fixtures."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.refresh import SourceCursor
from app.services.connection.sql_connector import SQLConnector


NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("dialect", "env_name"),
    [("postgres", "RUNTIME_POSTGRES_URL"), ("mysql", "RUNTIME_MYSQL_URL")],
)
def test_sql_polling_overlap_and_tuple_cursor_on_runtime_fixture(dialect, env_name):
    url = os.environ.get(env_name)
    if not url:
        pytest.skip(f"{env_name} is not configured")
    if dialect == "postgres":
        pytest.importorskip("psycopg2")
    else:
        pytest.importorskip("pymysql")

    connector = SQLConnector({
        "connection_string": url,
        "source_id": "source-acme-erp",
        "cursor_contract": "watermark_primary_key",
        "watermark_column": "watermark",
        "primary_key_column": "row_id",
        "query": (
            "SELECT * FROM refresh_source_rows "
            "WHERE source_id = 'source-acme-erp' AND resource = 'purchase_orders'"
        ),
    })
    page = connector.pull_delta(
        "refresh_source_rows",
        cursor=SourceCursor(
            source_id="source-acme-erp", resource="purchase_orders",
            contract="watermark_primary_key", watermark="2026-08-25T22:00:00Z",
            primary_key="row-acme-000001", opaque_value=None, observed_at=NOW,
        ),
        overlap_window=timedelta(hours=1),
    )

    assert page.cursor_outcome == "advanced"
    assert page.candidate_cursor.primary_key == "row-acme-000002"
    assert {item.primary_key for item in page.envelopes} == {
        "row-acme-000001", "row-acme-000002",
    }
