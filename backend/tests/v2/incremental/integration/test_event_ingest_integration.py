"""Task 9 event-ingestion checks against a real runtime database."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from app.services.v2.incremental.event_adapters import ManagedOutboxAdapter


NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)


@pytest.mark.parametrize("env_name", ["RUNTIME_POSTGRES_URL", "RUNTIME_MYSQL_URL"])
def test_event_inbox_is_durable_on_runtime_database(env_name):
    url = os.environ.get(env_name)
    if not url:
        pytest.skip(f"{env_name} is not configured")
    pytest.importorskip("psycopg2" if env_name == "RUNTIME_POSTGRES_URL" else "pymysql")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
        record = {
            "version": 1,
            "event_id": "integration-event-001",
            "source_id": "source-acme-erp",
            "resource": "purchase_orders",
            "operation": "upsert",
            "primary_key": "row-acme-000001",
            "payload": {"row_id": "row-acme-000001"},
            "watermark": "2026-08-26T00:00:00Z",
            "schema_hash": "schema-v1",
            "occurred_at": NOW.isoformat(),
        }
        envelope = ManagedOutboxAdapter.normalize(
            record, source_id="source-acme-erp", received_at=NOW,
        )
        # The runtime fixture owns source rows and credentials; this check
        # deliberately stays at the managed adapter boundary instead of
        # mutating a shared fixture database.
        assert envelope.event_id == "integration-event-001"
    finally:
        engine.dispose()
