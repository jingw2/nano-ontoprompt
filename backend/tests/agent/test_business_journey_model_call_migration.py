"""Migration and database-constraint coverage for turn-scoped journey slots."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.exc import IntegrityError


MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / (
    "0041_business_journey_model_call_turn_scope.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("business_journey_turn_scope", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_table(metadata: MetaData) -> Table:
    return Table(
        "business_journey_model_calls",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("run_id", String(200), nullable=False),
        Column("journey_id", String(100), nullable=False),
        Column("logical_call_index", Integer, nullable=False),
        Column("phase", String(20), nullable=False),
        Column("status", String(20), nullable=False),
        Column("call_kind", String(40), nullable=False),
        Column("correlation_id", String(400), nullable=False),
        Column("model_config_version_id", String(36), nullable=False),
        Column("requested_model", String(200)),
        Column("observed_model", String(200)),
        Column("http_attempts", Integer),
        Column("retry_count", Integer),
        Column("created_at", DateTime, nullable=False),
        Column("finalized_at", DateTime),
        UniqueConstraint(
            "run_id", "journey_id", "logical_call_index",
            name="uq_business_journey_model_call_slot",
        ),
    )


def _row(row_id: str, index: int, phase: str) -> dict:
    return {
        "id": row_id, "run_id": "migration-run", "journey_id": "credit",
        "logical_call_index": index, "phase": phase, "status": "finalized",
        "call_kind": "ontology" if phase == "preparation" else "agent_initial",
        "correlation_id": row_id, "model_config_version_id": "model-version",
        "created_at": datetime.now(timezone.utc),
    }


def test_turn_scope_migration_backfills_legacy_rows_and_round_trips_on_sqlite():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    table = _legacy_table(metadata)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(table.insert(), [_row("prep", 1, "preparation"), _row("legacy", 2, "runtime")])

    migration = _load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        rows = connection.execute(text(
            "SELECT id, turn_id FROM business_journey_model_calls ORDER BY id"
        )).all()
        assert rows == [("legacy", "__legacy__:legacy"), ("prep", "__preparation__")]
        assert "turn_id" in {column["name"] for column in inspect(connection).get_columns(
            "business_journey_model_calls"
        )}
        assert inspect(connection).get_unique_constraints("business_journey_model_calls") == [{
            "name": "uq_business_journey_model_call_turn_slot",
            "column_names": ["run_id", "journey_id", "turn_id", "logical_call_index"],
        }]

        # A new real turn can reuse the old runtime logical index, while a
        # duplicate attempt in the same turn remains rejected by the DB.
        values = {
            "run_id": "migration-run", "journey_id": "credit", "turn_id": "turn-a",
            "logical_call_index": 2, "phase": "runtime", "status": "reserved",
            "call_kind": "agent_initial", "correlation_id": "turn-a",
            "model_config_version_id": "model-version", "created_at": datetime.now(timezone.utc),
        }
        connection.execute(text(
            "INSERT INTO business_journey_model_calls "
            "(id, run_id, journey_id, turn_id, logical_call_index, phase, status, call_kind, "
            "correlation_id, model_config_version_id, created_at) "
            "VALUES ('turn-a-slot', :run_id, :journey_id, :turn_id, :logical_call_index, :phase, "
            ":status, :call_kind, :correlation_id, :model_config_version_id, :created_at)"
        ), values)
        values["turn_id"] = "turn-b"
        values["correlation_id"] = "turn-b"
        connection.execute(text(
            "INSERT INTO business_journey_model_calls "
            "(id, run_id, journey_id, turn_id, logical_call_index, phase, status, call_kind, "
            "correlation_id, model_config_version_id, created_at) "
            "VALUES ('turn-b-slot', :run_id, :journey_id, :turn_id, :logical_call_index, :phase, "
            ":status, :call_kind, :correlation_id, :model_config_version_id, :created_at)"
        ), values)
        values["id"] = "turn-a-slot-duplicate"
        values["turn_id"] = "turn-a"
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO business_journey_model_calls "
                "(id, run_id, journey_id, turn_id, logical_call_index, phase, status, call_kind, "
                "correlation_id, model_config_version_id, created_at) "
                "VALUES (:id, :run_id, :journey_id, :turn_id, :logical_call_index, :phase, "
                ":status, :call_kind, :correlation_id, :model_config_version_id, :created_at)"
            ), values)

        # Downgrade is deliberately fail-closed once multiple turn-scoped
        # rows exist: collapsing them would destroy historical evidence.
        with pytest.raises(RuntimeError, match="CANNOT_DOWNGRADE"):
            migration.downgrade()

    # A legacy-shaped populated table with only one row can still downgrade
    # cleanly, which covers the reversible migration path.
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    table = _legacy_table(metadata)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(table.insert(), [_row("prep", 1, "preparation")])
    migration = _load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.downgrade()
        columns = {column["name"] for column in inspect(connection).get_columns(
            "business_journey_model_calls"
        )}
        assert "turn_id" not in columns
        assert inspect(connection).get_unique_constraints("business_journey_model_calls") == [{
            "name": "uq_business_journey_model_call_slot",
            "column_names": ["run_id", "journey_id", "logical_call_index"],
        }]
