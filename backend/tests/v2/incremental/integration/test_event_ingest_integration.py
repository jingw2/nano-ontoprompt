"""Task 9 event-ingestion checks against migrated PostgreSQL/MySQL fixtures.

The runtime fixture databases contain source tables, not the application
schema. Each test therefore creates a disposable schema/database, bootstraps
the pre-refresh v2 tables, runs Alembic from revision 0021 through head, and
then drives the real event inbox service against that migrated database. No
shared runtime-fixture rows are changed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    JSON,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    text,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import sessionmaker

from app.models.v2.connection import Connection
from app.models.v2.dataset import Dataset
from app.models.v2.pipeline import Pipeline
from app.models.v2.refresh import RefreshDeadLetter, RefreshInboxEvent, RefreshRun
from app.services.v2.incremental.event_adapters import ManagedOutboxAdapter
from app.services.v2.incremental.event_ingest import EventIngestService


NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)
BACKEND_DIR = Path(__file__).resolve().parents[4]
MIGRATION_BASE = "0021_mapping_entity_class_cn"


def _migration_head() -> str:
    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from verify_build_manifest import resolve_alembic_head

    return resolve_alembic_head(BACKEND_DIR / "alembic")


def _pre_refresh_tables() -> MetaData:
    """Return the small baseline needed to run migrations 0021 through head.

    The fixture schema is intentionally not a copy of the application
    database. These are the v2/semantic/runtime-identity tables that exist
    before revision 0021; Alembic owns creation of every refresh, snapshot,
    and runtime table from there through head. This list must grow whenever
    a later migration references a pre-existing table/column this fixture
    doesn't yet define — it is not scoped to any single task's migrations.
    """
    metadata = MetaData()
    # Task 11's migration backfills OntologyRelease.status from the current
    # publication pointer and adds snapshot foreign keys.  This integration
    # fixture starts at 0021 with only the tables needed by the refresh path,
    # so provide the small semantic foundation it references.
    Table(
        "users", metadata,
        Column("id", String(36), primary_key=True),
    )
    Table(
        "security_domains", metadata,
        Column("id", String(36), primary_key=True),
    )
    Table(
        "ontology_projects", metadata,
        Column("id", String(36), primary_key=True),
        Column("latest_published_release_id", String(36), nullable=True),
    )
    Table(
        "ontology_releases", metadata,
        Column("id", String(36), primary_key=True),
        Column("ontology_id", String(36), nullable=False),
        Column("version_no", BigInteger, nullable=False),
        Column("manifest_bytes", LargeBinary(), nullable=False),
        Column("schema_hash", LargeBinary(), nullable=False),
        Column("created_by", String(36), nullable=False),
    )
    # Task 13's migration (0029) extends oauth_clients with a runtime
    # identity contract (security_domain_id FK, allowed_audiences,
    # capability_names); later migrations (0030, 0033) FK into it as the
    # registered Agent/service identity.
    Table(
        "oauth_clients", metadata,
        Column("id", String(36), primary_key=True),
        Column("client_name", String(200), nullable=False),
        Column("redirect_uris", JSON, nullable=False),
        Column("allowed_scopes", JSON, nullable=False),
        Column("is_active", Boolean, nullable=False),
        Column("created_by", String(36), nullable=False),
    )
    # Referenced by runtime_plans/sandbox_simulations/managed_action_bindings
    # (0030, 0032, 0033) as the Action a writable plan proposes. Task
    # 0042 drops execution_rule/function_code on this table, so both must
    # be present for that migration to succeed.
    Table(
        "actions", metadata,
        Column("id", String(36), primary_key=True),
        Column("ontology_id", String(36), nullable=False),
        Column("execution_rule", Text, nullable=True),
        Column("function_code", Text, nullable=True),
    )
    # Referenced by governed_turn_plans (0035) as the Agent turn/tool-call an
    # in-conversation governed action proposal is created from.
    Table(
        "agent_turns", metadata,
        Column("id", String(36), primary_key=True),
    )
    Table(
        "agent_tool_executions", metadata,
        Column("id", String(36), primary_key=True),
    )
    # Referenced by 0040 (adds title).
    Table(
        "agent_sessions", metadata,
        Column("id", String(36), primary_key=True),
    )
    # Referenced by 0052 (adds max_tool_rounds).
    Table(
        "agent_versions", metadata,
        Column("id", String(36), primary_key=True),
    )
    # Referenced by 0043/0045 (add tool_catalog_limit/entity_search_depth).
    Table(
        "agent_ontology_bindings", metadata,
        Column("id", String(36), primary_key=True),
    )
    # Referenced by 0042, which drops the pre-existing `formula` column
    # after backfilling `definition` from it.
    Table(
        "logic_rules", metadata,
        Column("id", String(36), primary_key=True),
        Column("formula", Text, nullable=True),
    )
    # Referenced by 0050 (adds scan_report).
    Table(
        "skill_versions", metadata,
        Column("id", String(36), primary_key=True),
    )
    # Referenced by 0047/0048 (add/alter the search_provider check constraint).
    Table(
        "tool_connection_versions", metadata,
        Column("id", String(36), primary_key=True),
    )
    # Referenced by 0051 (adds name).
    Table(
        "tool_connections", metadata,
        Column("id", String(36), primary_key=True),
    )
    # Referenced by 0049, which drops and recreates the pre-existing
    # ck_tool_providers_kind check constraint (originally added by 0012,
    # before this fixture's MIGRATION_BASE).
    Table(
        "tool_providers", metadata,
        Column("id", String(36), primary_key=True),
        Column("kind", String(20), nullable=False, server_default="search"),
        CheckConstraint(
            "kind IN ('search', 'playwright', 'skill', 'external_mcp', 'ontology_mcp')",
            name="ck_tool_providers_kind",
        ),
    )
    # Referenced by 0037 (adds pipeline_run_id).
    Table(
        "v2_curated_reviews", metadata,
        Column("id", String(36), primary_key=True),
    )
    for table_name, columns in {
        "v2_connections": [
            Column("id", String(36), primary_key=True),
            Column("name", String(200), nullable=False),
            Column("kind", String(50), nullable=False),
            Column("config", JSON, nullable=False),
            Column("status", String(20)),
            Column("last_sync_at", DateTime(timezone=True)),
            Column("created_by", String(36)),
            Column("created_at", DateTime(timezone=True)),
            Column("updated_at", DateTime(timezone=True)),
        ],
        "v2_datasets": [
            Column("id", String(36), primary_key=True),
            Column("name", String(200), nullable=False),
            Column("source_connection_id", String(36)),
            Column("kind", String(30), nullable=False),
            Column("schema_json", JSON),
            Column("latest_version_id", String(36)),
            Column("created_at", DateTime(timezone=True)),
            Column("updated_at", DateTime(timezone=True)),
        ],
        "v2_dataset_versions": [
            Column("id", String(36), primary_key=True),
            Column("dataset_id", String(36), nullable=False),
            Column("version_no", Integer, nullable=False),
            Column("rowcount", BigInteger),
            Column("storage_uri", Text),
            Column("checksum", String(64)),
            Column("created_at", DateTime(timezone=True)),
        ],
        "v2_pipelines": [
            Column("id", String(36), primary_key=True),
            Column("name", String(200), nullable=False),
            Column("domain", String(100)),
            Column("description", Text),
            Column("source_dataset_id", String(36)),
            Column("route", String(1)),
            Column("spec", JSON, nullable=False),
            Column("definition", JSON),
            Column("target_curated_ids", JSON),
            Column("schedule_cron", String(100)),
            Column("status", String(20)),
            Column("branch", String(50)),
            Column("version", Integer),
            Column("created_by", String(36)),
            Column("created_at", DateTime(timezone=True)),
            Column("updated_at", DateTime(timezone=True)),
        ],
        "v2_pipeline_runs": [
            Column("id", String(36), primary_key=True),
            Column("pipeline_id", String(36), nullable=False),
            Column("status", String(20), nullable=False),
            Column("started_at", DateTime(timezone=True)),
            Column("finished_at", DateTime(timezone=True)),
            Column("stats", JSON),
            Column("error_log", Text),
            Column("dataset_version_id", String(36)),
            Column("created_at", DateTime(timezone=True)),
        ],
    }.items():
        Table(table_name, metadata, *columns)
    return metadata


def _scoped_postgres_url(url: str, schema: str) -> str:
    """Pin both application tables and Alembic's version table to ``schema``."""
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _mysql_admin_url(url: URL) -> URL:
    configured = os.environ.get("RUNTIME_MYSQL_ADMIN_URL")
    if configured:
        return make_url(configured)
    # The committed runtime fixture explicitly documents runtime/runtime.
    # Derive its disposable-database admin URL only for that test sentinel;
    # arbitrary caller credentials are never rewritten.
    if url.username == "runtime" and url.password == "runtime":
        return url.set(username="root", database="mysql")
    pytest.skip("RUNTIME_MYSQL_ADMIN_URL is required to create a disposable migrated database")


def _migrate(url: str) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_migrations.py", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=url),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"migration failed:\n{result.stdout}\n{result.stderr}"


def _record(event_id: str, *, watermark: str = "2026-08-26T01:00:00Z") -> dict[str, object]:
    return {
        "version": 1,
        "event_id": event_id,
        "source_id": "task9-runtime-source",
        "resource": "orders",
        "operation": "upsert",
        "primary_key": event_id,
        "payload": {"id": event_id, "state": "ready"},
        "watermark": watermark,
        "schema_hash": "schema-v1",
        "occurred_at": NOW.isoformat(),
    }


@pytest.mark.parametrize(
    ("dialect", "env_name"),
    [("postgres", "RUNTIME_POSTGRES_URL"), ("mysql", "RUNTIME_MYSQL_URL")],
)
def test_migrated_runtime_inbox_accept_process_and_replay(dialect: str, env_name: str):
    """Exercise durable acceptance, processing, and DLQ replay on each dialect."""
    source_url = os.environ.get(env_name)
    if not source_url:
        pytest.skip(f"{env_name} is not configured")
    pytest.importorskip("psycopg2" if dialect == "postgres" else "pymysql")

    source = make_url(source_url)
    resource_name = f"task9_runtime_{dialect}_{uuid.uuid4().hex}"
    admin_engine = None
    app_url: str
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
                {"revision": MIGRATION_BASE},
            )
        _migrate(app_url)
        with app_engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _migration_head()

        Session = sessionmaker(bind=app_engine)
        db = Session()
        try:
            source_id = "task9-runtime-source"
            dataset_id = f"task9-dataset-{dialect}"
            pipeline_id = f"task9-pipeline-{dialect}"
            db.add_all([
                Connection(
                    id=source_id, name="Task 9 runtime source", kind="rest", status="active",
                    config={"cursor_contract": "watermark_primary_key", "schema_hash": "schema-v1"},
                ),
                Dataset(
                    id=dataset_id, name="orders", source_connection_id=source_id, kind="structured",
                ),
                Pipeline(
                    id=pipeline_id, name="Task 9 runtime pipeline", source_dataset_id=dataset_id,
                    spec={}, status="active",
                ),
            ])
            db.commit()

            service = EventIngestService()
            envelope = ManagedOutboxAdapter.normalize(
                _record(f"evt-{dialect}-first"), source_id=source_id, received_at=NOW,
            )
            accepted = service.accept(db, envelope, lease_owner="runtime-worker", now=NOW)
            assert accepted.status == "received"
            assert accepted.run_id is not None
            published: list[str] = []
            dispatched = service.dispatch_pending(
                db, run_id=accepted.run_id, dispatch=published.append, now=NOW,
            )
            assert dispatched.status == "received"
            assert published == [accepted.run_id]
            processed = service.process(
                db, run_id=accepted.run_id, lease_owner="runtime-worker", now=NOW,
            )
            assert processed.status == "succeeded"
            assert db.query(RefreshInboxEvent).one().state == "processed"

            replay_envelope = ManagedOutboxAdapter.normalize(
                _record(f"evt-{dialect}-dead-letter", watermark="2026-08-26T02:00:00Z"),
                source_id=source_id, received_at=NOW,
            )
            dead_letter_candidate = service.accept(
                db, replay_envelope, lease_owner="runtime-worker", now=NOW,
            )
            service.dead_letter(
                db, run_id=dead_letter_candidate.run_id, reason="runtime fixture failure", now=NOW,
            )
            dead_letter = db.query(RefreshDeadLetter).one()
            replay = service.replay_dead_letter(
                db, dead_letter_id=dead_letter.id, operator_id="runtime-operator", now=NOW,
            )
            assert replay.id != dead_letter_candidate.run_id
            assert db.get(RefreshDeadLetter, dead_letter.id).replay_run_id == replay.id
            service.dispatch_pending(db, run_id=replay.id, dispatch=lambda _run_id: None, now=NOW)
            replay_result = service.process(
                db, run_id=replay.id, lease_owner=replay.lease_owner, now=NOW,
            )
            assert replay_result.status == "succeeded"
            assert db.query(RefreshRun).count() == 3
        finally:
            db.close()
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
