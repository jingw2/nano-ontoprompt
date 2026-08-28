"""Task 11 schema contracts for immutable, governed semantic snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.ontology import OntologyProject
from app.models.ontology_release import OntologyRelease
from app.models.semantic_snapshot import SemanticSnapshot, SemanticSnapshotInput
from app.models.user import User
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


def _seed_ontology_release(
    db, *, release_id: str, status: str = "published", ontology_id: str | None = None,
) -> None:
    """Seed the User/OntologyProject/OntologyRelease chain a snapshot pins to.

    Inserts ontology_releases via raw SQL (mirroring
    app/services/publication/compiler.py) rather than the ORM model: the
    manifest_projection column's CanonicalJSONB type always renders a
    PostgreSQL `CAST(... AS JSONB)` in its bind_expression, which the SQLite
    test harness cannot compile.
    """
    if db.get(User, "user-001") is None:
        db.add(User(
            id="user-001", username="user-001", email="user-001@test.com",
            password_hash="x", role="editor",
        ))
    ontology_id = ontology_id or f"ontology-{release_id}"
    if db.get(OntologyProject, ontology_id) is None:
        db.add(OntologyProject(id=ontology_id, name="ontology", domain="test", created_by="user-001"))
    db.flush()
    manifest_bytes = f"manifest-{release_id}".encode()
    db.execute(
        text(
            "INSERT INTO ontology_releases "
            "(id, ontology_id, version_no, version, manifest_bytes, manifest_projection, "
            "schema_hash, status, created_by, created_at) "
            "VALUES (:id, :ontology_id, 1, 'v1', :manifest_bytes, '{}', :schema_hash, "
            ":status, 'user-001', :created_at)"
        ),
        {
            "id": release_id,
            "ontology_id": ontology_id,
            "manifest_bytes": manifest_bytes,
            "schema_hash": hashlib.sha256(manifest_bytes).digest(),
            "status": status,
            "created_at": datetime.now(timezone.utc),
        },
    )
    db.flush()


@pytest.fixture(autouse=True)
def _default_published_release(db):
    """Every test in this module pins snapshots to a published release unless
    it explicitly seeds a different one."""
    _seed_ontology_release(db, release_id="release-valid-001", status="published")


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


def test_snapshot_rejects_draft_ontology_release(db):
    """SNAPSHOT_RELEASE_NOT_PUBLISHED: a snapshot cannot pin a draft release."""
    _seed_ontology_release(db, release_id="release-draft-001", status="draft")
    db.add(SemanticSnapshot(
        id="snap-draft-001",
        ontology_release_id="release-draft-001",
        quality_summary={},
        evidence_summary={},
        materialization_hash="c" * 64,
        created_by="user-001",
    ))
    with pytest.raises(ValueError, match="SNAPSHOT_RELEASE_NOT_PUBLISHED"):
        db.commit()
    db.rollback()


def test_snapshot_rejects_revoked_ontology_release(db):
    """SNAPSHOT_RELEASE_NOT_PUBLISHED: a snapshot cannot pin a revoked release."""
    _seed_ontology_release(db, release_id="release-revoked-001", status="revoked")
    db.add(SemanticSnapshot(
        id="snap-revoked-001",
        ontology_release_id="release-revoked-001",
        quality_summary={},
        evidence_summary={},
        materialization_hash="d" * 64,
        created_by="user-001",
    ))
    with pytest.raises(ValueError, match="SNAPSHOT_RELEASE_NOT_PUBLISHED"):
        db.commit()
    db.rollback()


def test_snapshot_accepts_published_ontology_release(db):
    """The passing case: a snapshot pinning a published release commits cleanly."""
    db.add(_snapshot())
    db.commit()
    assert db.get(SemanticSnapshot, "snap-valid-001").ontology_release_id == "release-valid-001"


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


def test_manual_pipeline_run_without_pins_persists_output_and_lineage(db, monkeypatch):
    from app.services.v2 import dataset_service
    from app.tasks.v2 import pipeline_run as pipeline_module

    class MemoryStorage:
        def put_bytes(self, bucket, key, data, content_type="application/octet-stream"):
            return f"s3://{bucket}/{key}"

    monkeypatch.setattr("app.database.SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(dataset_service, "get_storage_service", lambda: MemoryStorage())
    monkeypatch.setattr(pipeline_module, "_load_source_rows", lambda *args, **kwargs: [{"id": "row-001"}])
    monkeypatch.setattr(
        pipeline_module,
        "_execute_route",
        lambda route, context, data: (data, context),
    )

    pipeline = Pipeline(
        id="pipeline-manual-001",
        name="manual pipeline",
        source_dataset_id="dataset-source-001",
        route="A",
        spec={},
        status="active",
    )
    source_dataset = Dataset(
        id="dataset-source-001",
        name="source",
        kind="structured",
    )
    source_version = DatasetVersion(
        id="dataset-source-version-001",
        dataset_id=source_dataset.id,
        version_no=1,
        rowcount=1,
    )
    source_dataset.latest_version_id = source_version.id
    run = PipelineRun(
        id="pipeline-run-manual-001",
        pipeline_id=pipeline.id,
        status="pending",
    )
    db.add_all([pipeline, source_dataset, source_version, run])
    db.commit()

    pipeline_module.pipeline_run_task.run(pipeline.id, run.id)
    db.expire_all()

    persisted_run = db.get(PipelineRun, run.id)
    assert persisted_run.status == "success"
    assert persisted_run.finished_at is not None
    assert persisted_run.dataset_version_id is not None
    output_version = db.get(DatasetVersion, persisted_run.dataset_version_id)
    assert output_version is not None
    assert output_version.dataset_id != source_dataset.id
    assert db.query(PipelineRunInput).filter(
        PipelineRunInput.pipeline_run_id == run.id,
        PipelineRunInput.dataset_version_id == source_version.id,
    ).one()

    db.add(_snapshot(snapshot_id="snap-manual-001"))
    db.commit()
    db.add(SemanticSnapshotInput(
        snapshot_id="snap-manual-001",
        dataset_version_id=source_version.id,
        pipeline_run_id=run.id,
    ))
    db.commit()


def test_manual_pipeline_run_pins_one_source_version_for_load_and_lineage(db, monkeypatch):
    from app.services.v2 import dataset_service
    from app.services.v2.incremental import polling
    from app.tasks.v2 import pipeline_run as pipeline_module

    class MemoryStorage:
        def put_bytes(self, bucket, key, data, content_type="application/octet-stream"):
            return f"s3://{bucket}/{key}"

    monkeypatch.setattr("app.database.SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(dataset_service, "get_storage_service", lambda: MemoryStorage())
    monkeypatch.setattr(
        pipeline_module,
        "_execute_route",
        lambda route, context, data: (data, context),
    )

    pipeline = Pipeline(
        id="pipeline-race-001",
        name="racing pipeline",
        source_dataset_id="dataset-race-001",
        route="A",
        spec={},
        status="active",
    )
    source_dataset = Dataset(id="dataset-race-001", name="race source", kind="structured")
    source_version_a = DatasetVersion(
        id="dataset-race-version-a",
        dataset_id=source_dataset.id,
        version_no=1,
        rowcount=1,
    )
    source_version_b = DatasetVersion(
        id="dataset-race-version-b",
        dataset_id=source_dataset.id,
        version_no=2,
        rowcount=1,
    )
    source_dataset.latest_version_id = source_version_a.id
    run = PipelineRun(
        id="pipeline-run-race-001",
        pipeline_id=pipeline.id,
        status="pending",
    )
    db.add_all([pipeline, source_dataset, source_version_a, source_version_b, run])
    db.commit()

    selector_calls = 0
    loaded_version_ids = []

    def select_version_with_later_update(*args, **kwargs):
        nonlocal selector_calls
        selector_calls += 1
        return source_version_a if selector_calls == 1 else source_version_b

    monkeypatch.setattr(polling, "select_pinned_dataset_version", select_version_with_later_update)

    def load_rows_using_selected_version(*args, selected_version=None, **kwargs):
        if selected_version is None:
            selected_version = polling.select_pinned_dataset_version(
                args[0], dataset_id=args[2]["dataset_id"], input_dataset_version_ids=None,
            )
        loaded_version_ids.append(selected_version.id)
        return [{"version_id": selected_version.id}]

    monkeypatch.setattr(pipeline_module, "_load_source_rows", load_rows_using_selected_version)

    pipeline_module.pipeline_run_task.run(pipeline.id, run.id)
    db.expire_all()

    persisted_run = db.get(PipelineRun, run.id)
    assert persisted_run.status == "success"
    assert loaded_version_ids == [source_version_a.id]
    assert selector_calls == 1
    assert db.query(PipelineRunInput).filter(
        PipelineRunInput.pipeline_run_id == run.id,
        PipelineRunInput.dataset_version_id == source_version_a.id,
    ).one()
    assert db.query(PipelineRunInput).filter(
        PipelineRunInput.pipeline_run_id == run.id,
        PipelineRunInput.dataset_version_id == source_version_b.id,
    ).first() is None


def test_manual_pipeline_run_loads_exact_duplicate_version_id(db, monkeypatch):
    from app.services.v2 import dataset_service
    from app.tasks.v2 import pipeline_run as pipeline_module

    class MemoryStorage:
        def __init__(self):
            self.objects = {}

        def put_bytes(self, bucket, key, data, content_type="application/octet-stream"):
            self.objects[f"s3://{bucket}/{key}"] = data
            return f"s3://{bucket}/{key}"

        def get_object(self, uri):
            return self.objects[uri]

    monkeypatch.setattr("app.database.SessionLocal", sessionmaker(bind=db.get_bind()))
    storage = MemoryStorage()
    monkeypatch.setattr(dataset_service, "get_storage_service", lambda: storage)
    captured_context = []
    monkeypatch.setattr(
        pipeline_module,
        "_execute_route",
        lambda route, context, data: (captured_context.append((context, data)) or (data, context)),
    )

    pipeline = Pipeline(
        id="pipeline-duplicate-version-001",
        name="duplicate version pipeline",
        source_dataset_id="dataset-duplicate-version-001",
        route="A",
        spec={},
        status="active",
    )
    source_dataset = Dataset(
        id="dataset-duplicate-version-001",
        name="duplicate version source",
        kind="structured",
    )
    source_version_a = DatasetVersion(
        id="dataset-duplicate-version-a",
        dataset_id=source_dataset.id,
        version_no=1,
        rowcount=1,
    )
    source_version_b = DatasetVersion(
        id="dataset-duplicate-version-b",
        dataset_id=source_dataset.id,
        version_no=1,
        rowcount=1,
    )
    source_dataset.latest_version_id = source_version_a.id
    run = PipelineRun(
        id="pipeline-run-duplicate-version-001",
        pipeline_id=pipeline.id,
        status="pending",
    )
    db.add_all([pipeline, source_dataset, source_version_a, source_version_b, run])
    db.commit()

    def preview_ambiguous_version(self, dataset_id, version_no, limit=100, *, version_id=None):
        loaded_id = version_id or source_version_b.id
        return [{
            "source_version_id": loaded_id,
            "value": "from-A" if loaded_id == source_version_a.id else "from-B",
        }]

    monkeypatch.setattr(dataset_service.DatasetService, "preview", preview_ambiguous_version)

    pipeline_module.pipeline_run_task.run(pipeline.id, run.id)
    db.expire_all()

    persisted_run = db.get(PipelineRun, run.id)
    assert persisted_run.status == "success"
    output_version = db.get(DatasetVersion, persisted_run.dataset_version_id)
    assert output_version is not None
    output_bytes = storage.get_object(output_version.storage_uri)
    assert b"from-A" in output_bytes
    assert b"from-B" not in output_bytes
    context, loaded_rows = captured_context[0]
    assert loaded_rows == [{"source_version_id": source_version_a.id, "value": "from-A"}]
    assert context.dataset_version_id == source_version_a.id
    assert db.query(PipelineRunInput).filter(
        PipelineRunInput.pipeline_run_id == run.id,
        PipelineRunInput.dataset_version_id == source_version_a.id,
    ).one()
    assert db.query(PipelineRunInput).filter(
        PipelineRunInput.pipeline_run_id == run.id,
        PipelineRunInput.dataset_version_id == source_version_b.id,
    ).first() is None


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


def _load_task_11_fix_migration():
    migration_path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0028_snapshot_release_published.py"
    spec = importlib.util.spec_from_file_location("task_11_fix_snapshot_release_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(migration)
    return migration


def test_sqlite_migration_guard_rejects_snapshot_with_unpublished_release(db):
    """SNAPSHOT_RELEASE_NOT_PUBLISHED must also hold for raw SQL that bypasses the ORM."""
    _seed_ontology_release(db, release_id="release-draft-guard-001", status="draft")
    db.commit()

    migration = _load_task_11_fix_migration()
    context = MigrationContext.configure(db.connection())
    migration.op = Operations(context)
    migration._create_sqlite_guard()

    with pytest.raises(IntegrityError, match="SNAPSHOT_RELEASE_NOT_PUBLISHED"):
        db.execute(text(
            "INSERT INTO semantic_snapshots "
            "(id, ontology_release_id, quality_summary, evidence_summary, "
            "materialization_hash, status, created_by, created_at) "
            "VALUES ('snap-guard-draft-001', 'release-draft-guard-001', '{}', '{}', "
            f"'{'e' * 64}', 'materialized', 'user-001', CURRENT_TIMESTAMP)"
        ))
        db.commit()
    db.rollback()

    # A published release still inserts cleanly through the same guard.
    db.execute(text(
        "INSERT INTO semantic_snapshots "
        "(id, ontology_release_id, quality_summary, evidence_summary, "
        "materialization_hash, status, created_by, created_at) "
        "VALUES ('snap-guard-published-001', 'release-valid-001', '{}', '{}', "
        f"'{'f' * 64}', 'materialized', 'user-001', CURRENT_TIMESTAMP)"
    ))
    db.commit()
    assert db.get(SemanticSnapshot, "snap-guard-published-001") is not None


def _sqlite_pre_0027_engine(*, foreign_keys=False):
    engine = create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.String, primary_key=True))
    sa.Table(
        "ontology_projects", metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("latest_published_release_id", sa.String),
    )
    sa.Table(
        "ontology_releases", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ontology_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("manifest_bytes", sa.LargeBinary, nullable=False),
        sa.Column("manifest_projection", sa.JSON, nullable=False),
        sa.Column("schema_hash", sa.LargeBinary, nullable=False),
        sa.Column("created_by", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    sa.Table("v2_pipelines", metadata, sa.Column("id", sa.String, primary_key=True))
    sa.Table("v2_dataset_versions", metadata, sa.Column("id", sa.String, primary_key=True))
    sa.Table(
        "v2_pipeline_runs", metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("pipeline_id", sa.String, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("finished_at", sa.DateTime),
        sa.Column("stats", sa.JSON),
        sa.Column("error_log", sa.Text),
        sa.Column("dataset_version_id", sa.String),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    sa.Table(
        "pipeline_run_inputs", metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("pipeline_run_id", sa.String, nullable=False),
        sa.Column("dataset_version_id", sa.String, nullable=False),
        sa.Column("input_ordinal", sa.Integer, nullable=False),
    )
    if foreign_keys:
        sa.Table(
            "mcp_write_requests", metadata,
            sa.Column("id", sa.String, primary_key=True),
            sa.Column(
                "release_id", sa.String(36),
                sa.ForeignKey("ontology_releases.id", ondelete="RESTRICT"),
                nullable=False,
            ),
        )
    metadata.create_all(engine)
    if foreign_keys:
        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
    return engine


def test_sqlite_migration_upgrade_and_downgrade_round_trip_remediates_rows():
    engine = _sqlite_pre_0027_engine(foreign_keys=True)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id) VALUES ('user-001')"
        ))
        connection.execute(text(
            "INSERT INTO ontology_projects (id, latest_published_release_id) "
            "VALUES ('ontology-001', 'release-001')"
        ))
        connection.execute(text(
            "INSERT INTO ontology_releases "
            "(id, ontology_id, version_no, version, manifest_bytes, manifest_projection, schema_hash, created_by, created_at) "
            "VALUES ('release-001', 'ontology-001', 1, 'v1', :manifest, '{}', :schema_hash, 'user-001', :created_at)"
        ), {
            "manifest": b"immutable-manifest",
            "schema_hash": b"schema-hash",
            "created_at": now,
        })
        connection.execute(text(
            "INSERT INTO mcp_write_requests (id, release_id) "
            "VALUES ('mcp-write-001', 'release-001')"
        ))
        connection.execute(text(
            "INSERT INTO v2_pipelines (id) VALUES ('pipeline-001')"
        ))
        connection.execute(text(
            "INSERT INTO v2_dataset_versions (id) VALUES ('dataset-version-001')"
        ))
        connection.execute(text(
            "INSERT INTO v2_pipeline_runs "
            "(id, pipeline_id, status, finished_at, dataset_version_id, created_at) "
            "VALUES ('pipeline-run-valid', 'pipeline-001', 'success', :finished_at, "
            "'dataset-version-001', :created_at), "
            "('pipeline-run-invalid', 'pipeline-001', 'success', NULL, NULL, :created_at)"
        ), {"finished_at": now, "created_at": now})
        connection.execute(text(
            "INSERT INTO pipeline_run_inputs "
            "(id, pipeline_run_id, dataset_version_id, input_ordinal) "
            "VALUES ('pipeline-input-001', 'pipeline-run-valid', 'dataset-version-001', 0)"
        ))

    migration = _load_task_11_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='semantic_snapshots'"
        )).scalar_one() == "semantic_snapshots"
        assert connection.execute(text(
            "SELECT name FROM pragma_table_info('ontology_releases') WHERE name='status'"
        )).scalar_one() == "status"
        pipeline_run_sql = connection.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='v2_pipeline_runs'"
        )).scalar_one()
        assert "ck_v2_pipeline_runs_success_completion" in pipeline_run_sql
        assert "finished_at IS NOT NULL AND dataset_version_id IS NOT NULL" in pipeline_run_sql
        assert connection.execute(text(
            "SELECT status FROM ontology_releases WHERE id='release-001'"
        )).scalar_one() == "published"
        assert connection.execute(text(
            "SELECT release_id FROM mcp_write_requests WHERE id='mcp-write-001'"
        )).scalar_one() == "release-001"
        remediated = connection.execute(text(
            "SELECT status, error_log FROM v2_pipeline_runs WHERE id='pipeline-run-invalid'"
        )).one()
        assert remediated == ("failed", "MIGRATION_REMEDIATED_UNGOVERNED_SUCCESS")
        trigger_names = {
            row[0] for row in connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ))
        }
        assert {
            "semantic_snapshots_immutable_update",
            "semantic_snapshots_immutable_delete",
            "semantic_snapshot_inputs_immutable_update",
            "semantic_snapshot_inputs_immutable_delete",
            "semantic_snapshot_inputs_validate",
        } <= trigger_names

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.downgrade()

    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='semantic_snapshots'"
        )).scalar_one_or_none() is None
        assert connection.execute(text(
            "SELECT name FROM pragma_table_info('ontology_releases') WHERE name='status'"
        )).scalar_one_or_none() is None
        assert connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'semantic_snapshot%'"
        )).first() is None
        assert connection.execute(text(
            "SELECT release_id FROM mcp_write_requests WHERE id='mcp-write-001'"
        )).scalar_one() == "release-001"


def test_sqlite_migration_backfill_orders_draft_and_revoked_by_version_no():
    """A release strictly newer than the currently-published one (in-progress
    draft work created after publication) must backfill to 'draft', not
    'revoked'; a release strictly older than the published one must still
    backfill to 'revoked'."""
    engine = _sqlite_pre_0027_engine()
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id) VALUES ('user-001')"))
        connection.execute(text(
            "INSERT INTO ontology_projects (id, latest_published_release_id) "
            "VALUES ('ontology-001', 'release-published-001')"
        ))
        connection.execute(text(
            "INSERT INTO ontology_releases "
            "(id, ontology_id, version_no, version, manifest_bytes, manifest_projection, schema_hash, created_by, created_at) "
            "VALUES "
            "('release-published-001', 'ontology-001', 2, 'v2', :manifest, '{}', :schema_hash, 'user-001', :created_at), "
            "('release-older-001', 'ontology-001', 1, 'v1', :manifest, '{}', :schema_hash, 'user-001', :created_at), "
            "('release-newer-001', 'ontology-001', 3, 'v3', :manifest, '{}', :schema_hash, 'user-001', :created_at)"
        ), {"manifest": b"immutable-manifest", "schema_hash": b"schema-hash", "created_at": now})

    migration = _load_task_11_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

    with engine.connect() as connection:
        statuses = dict(connection.execute(text(
            "SELECT id, status FROM ontology_releases WHERE ontology_id='ontology-001'"
        )).all())
    assert statuses["release-published-001"] == "published"
    assert statuses["release-older-001"] == "revoked"
    assert statuses["release-newer-001"] == "draft"


def test_task_11_migration_is_next_linear_head():
    migration = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0027_semantic_snapshot.py"
    source = migration.read_text()
    assert 'revision = "0027_semantic_snapshot"' in source
    assert 'down_revision = "0026_refresh_event_inbox"' in source


def test_task_11_fix_migration_chains_off_semantic_snapshot_head():
    migration = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0028_snapshot_release_published.py"
    source = migration.read_text()
    assert 'revision = "0028_snapshot_release_published"' in source
    assert 'down_revision = "0027_semantic_snapshot"' in source


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
