"""Task 11 schema contracts for immutable, governed semantic snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.ontology_release import OntologyRelease
from app.models.semantic_snapshot import SemanticSnapshot, SemanticSnapshotInput
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineRunInput


def _snapshot(*, snapshot_id: str = "snap-valid-001") -> SemanticSnapshot:
    return SemanticSnapshot(
        id=snapshot_id,
        ontology_release_id="release-valid-001",
        quality_summary={"row_count": 2},
        evidence_summary={"citations": ["ev-001"]},
        materialization_hash="a" * 64,
        status="materialized",
        created_by="user-001",
    )


def _seed_governed_run(db) -> None:
    pipeline = Pipeline(id="pipeline-001", name="pipeline", spec={})
    dataset = Dataset(id="dataset-001", name="dataset", kind="structured")
    output = DatasetVersion(id="dataset-version-001", dataset_id=dataset.id, version_no=1)
    db.add_all([pipeline, dataset, output])
    db.flush()
    db.add(PipelineRun(
        id="pipeline-run-001",
        pipeline_id=pipeline.id,
        status="success",
        finished_at=datetime.now(timezone.utc),
        dataset_version_id=output.id,
    ))
    db.flush()
    db.add(PipelineRunInput(
        id="pipeline-run-input-001",
        pipeline_run_id="pipeline-run-001",
        dataset_version_id=output.id,
        input_ordinal=0,
    ))
    db.flush()


def test_snapshot_has_required_contract_and_complete_inputs(db):
    _seed_governed_run(db)
    snapshot = _snapshot()
    db.add(snapshot)
    db.commit()

    db.add(SemanticSnapshotInput(
        snapshot_id=snapshot.id,
        dataset_version_id="dataset-version-001",
        pipeline_run_id="pipeline-run-001",
    ))
    db.commit()

    assert snapshot.ontology_release_id == "release-valid-001"
    assert snapshot.materialization_hash == "a" * 64
    assert db.query(SemanticSnapshotInput).count() == 1


def test_snapshot_input_is_unique_per_dataset_and_run(db):
    _seed_governed_run(db)
    db.add(_snapshot())
    db.commit()
    db.add(SemanticSnapshotInput(
        snapshot_id="snap-valid-001",
        dataset_version_id="dataset-version-001",
        pipeline_run_id="pipeline-run-001",
    ))
    db.commit()

    db.add(SemanticSnapshotInput(
        snapshot_id="snap-valid-001",
        dataset_version_id="dataset-version-001",
        pipeline_run_id="pipeline-run-001",
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_snapshot_and_inputs_are_append_only(db):
    _seed_governed_run(db)
    db.add(_snapshot())
    db.commit()
    db.add(SemanticSnapshotInput(
        snapshot_id="snap-valid-001",
        dataset_version_id="dataset-version-001",
        pipeline_run_id="pipeline-run-001",
    ))
    db.commit()

    snapshot = db.get(SemanticSnapshot, "snap-valid-001")
    snapshot.quality_summary = {"row_count": 3}
    with pytest.raises((IntegrityError, ValueError)):
        db.commit()
    db.rollback()

    input_row = db.query(SemanticSnapshotInput).one()
    db.delete(input_row)
    with pytest.raises((IntegrityError, ValueError)):
        db.commit()
    db.rollback()

    db.delete(db.get(SemanticSnapshot, "snap-valid-001"))
    with pytest.raises((IntegrityError, ValueError)):
        db.commit()
    db.rollback()


def test_snapshot_input_requires_successful_completed_pipeline_output(db):
    """A snapshot input must point at the run's governed output version."""
    pipeline = Pipeline(id="pipeline-001", name="pipeline", spec={})
    dataset = Dataset(id="dataset-001", name="dataset", kind="structured")
    output = DatasetVersion(id="dataset-version-001", dataset_id=dataset.id, version_no=1)
    db.add_all([pipeline, dataset, output])
    db.flush()

    run = PipelineRun(
        id="pipeline-run-001",
        pipeline_id=pipeline.id,
        status="success",
        finished_at=datetime.now(timezone.utc),
        dataset_version_id=output.id,
    )
    db.add(run)
    db.flush()
    db.add(PipelineRunInput(
        id="pipeline-run-input-001",
        pipeline_run_id=run.id,
        dataset_version_id=output.id,
        input_ordinal=0,
    ))
    db.add(_snapshot())
    db.flush()
    db.add(SemanticSnapshotInput(
        snapshot_id="snap-valid-001",
        dataset_version_id=output.id,
        pipeline_run_id=run.id,
    ))
    db.commit()

    assert db.query(SemanticSnapshotInput).one().dataset_version_id == output.id

    db.add(SemanticSnapshot(
        id="snap-invalid-output",
        ontology_release_id="release-valid-001",
        quality_summary={},
        evidence_summary={},
        materialization_hash="b" * 64,
        status="materialized",
        created_by="user-001",
    ))
    db.commit()
    db.add(SemanticSnapshotInput(
        snapshot_id="snap-invalid-output",
        dataset_version_id="dataset-version-not-output",
        pipeline_run_id=run.id,
    ))
    with pytest.raises((IntegrityError, ValueError)):
        db.commit()
    db.rollback()


def test_snapshot_inputs_capture_all_versions_from_one_governed_multi_source_run(db):
    pipeline = Pipeline(id="pipeline-001", name="pipeline", spec={})
    first_dataset = Dataset(id="dataset-001", name="first", kind="structured")
    second_dataset = Dataset(id="dataset-002", name="second", kind="structured")
    first_version = DatasetVersion(id="dataset-version-001", dataset_id=first_dataset.id, version_no=1)
    second_version = DatasetVersion(id="dataset-version-002", dataset_id=second_dataset.id, version_no=1)
    db.add_all([pipeline, first_dataset, second_dataset, first_version, second_version])
    db.flush()
    db.add(PipelineRun(
        id="pipeline-run-001",
        pipeline_id=pipeline.id,
        status="success",
        finished_at=datetime.now(timezone.utc),
        dataset_version_id=first_version.id,
    ))
    db.flush()
    db.add_all([
        PipelineRunInput(
            id="pipeline-run-input-001",
            pipeline_run_id="pipeline-run-001",
            dataset_version_id=first_version.id,
            input_ordinal=0,
        ),
        PipelineRunInput(
            id="pipeline-run-input-002",
            pipeline_run_id="pipeline-run-001",
            dataset_version_id=second_version.id,
            input_ordinal=1,
        ),
    ])
    db.add(_snapshot())
    db.flush()
    db.add_all([
        SemanticSnapshotInput(
            snapshot_id="snap-valid-001",
            dataset_version_id=first_version.id,
            pipeline_run_id="pipeline-run-001",
        ),
        SemanticSnapshotInput(
            snapshot_id="snap-valid-001",
            dataset_version_id=second_version.id,
            pipeline_run_id="pipeline-run-001",
        ),
    ])
    db.commit()

    assert db.query(SemanticSnapshotInput).count() == 2


def test_ontology_release_status_contract_is_registered():
    columns = set(OntologyRelease.__table__.c.keys())
    assert "status" in columns
    constraints = {constraint.name for constraint in OntologyRelease.__table__.constraints}
    assert "ck_ontology_releases_status" in constraints


def test_pipeline_run_exposes_governed_completion_and_output_provenance():
    columns = set(PipelineRun.__table__.c.keys())
    assert {"status", "finished_at", "dataset_version_id"} <= columns
    constraints = {constraint.name for constraint in PipelineRun.__table__.constraints}
    assert "ck_v2_pipeline_runs_status" in constraints
    assert "ck_v2_pipeline_runs_success_completion" in constraints
    assert {
        "pipeline_run_id", "dataset_version_id", "source_cursor", "provenance", "input_ordinal",
    } <= set(PipelineRunInput.__table__.c.keys())


def test_pipeline_run_rejects_successful_output_without_completion(db):
    pipeline = Pipeline(id="pipeline-001", name="pipeline", spec={})
    dataset = Dataset(id="dataset-001", name="dataset", kind="structured")
    output = DatasetVersion(id="dataset-version-001", dataset_id=dataset.id, version_no=1)
    db.add_all([pipeline, dataset, output])
    db.flush()
    db.add(PipelineRun(
        id="pipeline-run-001",
        pipeline_id=pipeline.id,
        status="success",
        dataset_version_id=output.id,
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_pipeline_run_rejects_successful_completion_without_output(db):
    pipeline = Pipeline(id="pipeline-001", name="pipeline", spec={})
    db.add(pipeline)
    db.flush()
    db.add(PipelineRun(
        id="pipeline-run-001",
        pipeline_id=pipeline.id,
        status="success",
        finished_at=datetime.now(timezone.utc),
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def _load_task_11_migration():
    migration_path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0027_semantic_snapshot.py"
    spec = importlib.util.spec_from_file_location("task_11_snapshot_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(migration)
    return migration


def test_sqlite_migration_guards_reject_direct_snapshot_dml(db):
    _seed_governed_run(db)
    db.add(_snapshot())
    db.commit()
    db.add(SemanticSnapshotInput(
        snapshot_id="snap-valid-001",
        dataset_version_id="dataset-version-001",
        pipeline_run_id="pipeline-run-001",
    ))
    db.commit()

    migration = _load_task_11_migration()
    context = MigrationContext.configure(db.connection())
    migration.op = Operations(context)
    migration._create_sqlite_guards()

    with pytest.raises(IntegrityError, match="SEMANTIC_SNAPSHOT_IMMUTABLE"):
        db.execute(text("UPDATE semantic_snapshots SET quality_summary='{}' WHERE id='snap-valid-001'"))
        db.commit()
    db.rollback()

    with pytest.raises(IntegrityError, match="SEMANTIC_SNAPSHOT_IMMUTABLE"):
        db.execute(text("DELETE FROM semantic_snapshot_inputs WHERE snapshot_id='snap-valid-001'"))
        db.commit()
    db.rollback()

    with pytest.raises(IntegrityError, match="SEMANTIC_SNAPSHOT_IMMUTABLE"):
        db.execute(text(
            "UPDATE semantic_snapshot_inputs SET pipeline_run_id='pipeline-run-001' "
            "WHERE snapshot_id='snap-valid-001'"
        ))
        db.commit()
    db.rollback()

    with pytest.raises(IntegrityError, match="SEMANTIC_SNAPSHOT_IMMUTABLE"):
        db.execute(text("DELETE FROM semantic_snapshots WHERE id='snap-valid-001'"))
        db.commit()
    db.rollback()

    failed_run = PipelineRun(
        id="pipeline-run-failed",
        pipeline_id="pipeline-001",
        status="failed",
        finished_at=datetime.now(timezone.utc),
        dataset_version_id="dataset-version-001",
    )
    db.add(failed_run)
    db.commit()
    with pytest.raises(IntegrityError, match="SNAPSHOT_INPUT_NOT_GOVERNED"):
        db.execute(text(
            "INSERT INTO semantic_snapshot_inputs "
            "(id, snapshot_id, dataset_version_id, pipeline_run_id, created_at) "
            "VALUES ('input-failed-001', 'snap-valid-001', 'dataset-version-001', "
            "'pipeline-run-failed', CURRENT_TIMESTAMP)"
        ))
        db.commit()
    db.rollback()

    completed_run_without_lineage = PipelineRun(
        id="pipeline-run-no-lineage",
        pipeline_id="pipeline-001",
        status="success",
        finished_at=datetime.now(timezone.utc),
        dataset_version_id="dataset-version-001",
    )
    db.add(completed_run_without_lineage)
    db.commit()
    with pytest.raises(IntegrityError, match="SNAPSHOT_INPUT_NOT_GOVERNED"):
        db.execute(text(
            "INSERT INTO semantic_snapshot_inputs "
            "(id, snapshot_id, dataset_version_id, pipeline_run_id, created_at) "
            "VALUES ('input-no-lineage-001', 'snap-valid-001', 'dataset-version-001', "
            "'pipeline-run-no-lineage', CURRENT_TIMESTAMP)"
        ))
        db.commit()
    db.rollback()


def test_task_11_migration_is_next_linear_head():
    migration = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0027_semantic_snapshot.py"
    source = migration.read_text()
    assert 'revision = "0027_semantic_snapshot"' in source
    assert 'down_revision = "0026_refresh_event_inbox"' in source


def test_snapshot_hash_has_sha256_shape_constraint():
    names = {constraint.name for constraint in SemanticSnapshot.__table__.constraints}
    assert "ck_semantic_snapshots_materialization_hash" in names


def test_snapshot_rejects_non_hex_materialization_hash(db):
    db.add(SemanticSnapshot(
        id="snap-invalid-hash",
        ontology_release_id="release-valid-001",
        quality_summary={},
        evidence_summary={},
        materialization_hash="z" * 64,
        created_by="user-001",
    ))
    with pytest.raises(ValueError, match="INVALID_MATERIALIZATION_HASH"):
        db.commit()
    db.rollback()
