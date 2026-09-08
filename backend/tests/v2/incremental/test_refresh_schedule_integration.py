"""Isolated-database schedule dispatch integration test (Task 7).

`isolated_schedule_db` mirrors test_refresh_contract.py's `concurrent_refresh_db`
fixture: a real, disposable PostgreSQL schema migrated to head, used
directly by the production `schedule_service` functions exactly like the
SQLite `db` fixture, proving a schedule created in one isolated schema never
leaks into another test's schema/session.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.models.v2.refresh import RefreshSchedule
from app.services.v2.scheduler.schedule_service import ScheduleRequest, dispatch_due_schedules, upsert_refresh_schedule

FIXED_NOW = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)

BACKEND_DIR = Path(__file__).resolve().parents[3]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _migrate(schema: str) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_migrations.py", "upgrade", "head"],
        cwd=BACKEND_DIR, env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"migration failed:\n{result.stdout}\n{result.stderr}"


class IsolatedScheduleDB:
    """A real Session (used directly by schedule_service functions, like
    `db`) backed by its own disposable PostgreSQL schema."""

    def __init__(self, engine, schema: str):
        self._engine = engine
        self.schema = schema
        self.session = sessionmaker(bind=engine)()

    def __getattr__(self, name):
        return getattr(self.session, name)


@pytest.fixture
def isolated_schedule_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "refresh_schedule_" + uuid.uuid4().hex

    admin_engine = create_engine(TEST_DATABASE_URL)
    try:
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    finally:
        admin_engine.dispose()

    _migrate(schema)

    engine = create_engine(_scoped_url(schema))
    wrapper = IsolatedScheduleDB(engine, schema)
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

def create_fixture_schedule(db, *, target_id, max_pending_runs=5, cron_expr="* * * * *") -> RefreshSchedule:
    request = ScheduleRequest(
        target_type="connection", target_id=target_id, cron_expr=cron_expr, timezone="UTC",
        business_calendar=[], sla_seconds=0, retry_policy=None, backfill_window_seconds=0,
        max_pending_runs=max_pending_runs, enabled=True,
    )
    return upsert_refresh_schedule(db, request, now=FIXED_NOW - timedelta(minutes=1))


def record_sent_run(message) -> str:
    return message.run_id


def list_schedule_targets(db) -> list[str]:
    return sorted(db.execute(select(RefreshSchedule.target_id)).scalars().all())


# ── test ─────────────────────────────────────────────────────────────────

def test_schedule_integration_uses_isolated_database_and_no_shared_schedule_rows(isolated_schedule_db):
    create_fixture_schedule(isolated_schedule_db, target_id="source-isolated")
    dispatch_due_schedules(isolated_schedule_db, now=FIXED_NOW, lease_owner="isolated-beat",
                           send_refresh=record_sent_run)
    assert list_schedule_targets(isolated_schedule_db) == ["source-isolated"]
