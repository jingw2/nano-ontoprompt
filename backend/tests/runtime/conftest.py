"""Fixtures for the governed semantic-snapshot service tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.ontology import OntologyProject
from app.models.ontology_release import OntologyRelease
from app.models.user import User
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineRunInput


DEFAULT_SECURITY_DOMAIN = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def snapshot_user(db):
    user = User(
        id="user-001",
        username="snapshot-user",
        email="snapshot-user@example.invalid",
        password_hash="not-a-real-password-hash",
        role="editor",
        security_domain_id=DEFAULT_SECURITY_DOMAIN,
    )
    db.add(user)
    db.commit()
    return user


def _release(db, snapshot_user, *, release_id: str, status: str) -> OntologyRelease:
    project = OntologyProject(
        id="ontology-001" if status == "published" else "ontology-draft-001",
        name="snapshot ontology",
        domain="test",
        created_by=snapshot_user.id,
        security_domain_id=snapshot_user.security_domain_id,
    )
    release = OntologyRelease(
        id=release_id,
        ontology_id=project.id,
        version_no=1,
        version="v1",
        manifest_bytes=b"snapshot-manifest",
        manifest_projection="{}",
        schema_hash=b"snapshot-schema-hash-000000000000",
        status=status,
        created_by=snapshot_user.id,
    )
    project.latest_published_release_id = release.id if status == "published" else None
    # OntologyRelease.manifest_projection uses Task 11's PostgreSQL-only
    # CanonicalJSONB type.  The shared SQLite unit harness intentionally
    # creates a columns-only compatibility table, so seed this fixture with a
    # plain SQL insert rather than invoking the PostgreSQL cast expression.
    db.add(project)
    db.flush()
    db.execute(
        text(
            "INSERT INTO ontology_releases "
            "(id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, status, created_by, created_at) "
            "VALUES (:id, :ontology_id, :version_no, :version, :manifest, "
            ":projection, :schema_hash, :status, :created_by, CURRENT_TIMESTAMP)"
        ),
        {
            "id": release.id,
            "ontology_id": release.ontology_id,
            "version_no": release.version_no,
            "version": release.version,
            "manifest": release.manifest_bytes,
            "projection": "{}",
            "schema_hash": release.schema_hash,
            "status": release.status,
            "created_by": release.created_by,
        },
    )
    project.latest_published_release_id = release.id if status == "published" else None
    db.commit()
    return db.get(OntologyRelease, release_id)


@pytest.fixture
def valid_release(db, snapshot_user):
    return _release(db, snapshot_user, release_id="release-valid-001", status="published")


@pytest.fixture
def draft_release(db, snapshot_user):
    return _release(db, snapshot_user, release_id="release-draft-001", status="draft")


def _run(
    db,
    *,
    pipeline_id: str,
    dataset_id: str,
    dataset_version_id: str,
    pipeline_run_id: str,
    status: str = "success",
    provenance: dict | None = None,
    stats: dict | None = None,
):
    pipeline = Pipeline(id=pipeline_id, name=pipeline_id, spec={})
    dataset = Dataset(id=dataset_id, name=dataset_id, kind="structured")
    version = DatasetVersion(
        id=dataset_version_id,
        dataset_id=dataset.id,
        version_no=1,
        rowcount=2,
        checksum="a" * 64,
    )
    pipeline_run = PipelineRun(
        id=pipeline_run_id,
        pipeline_id=pipeline.id,
        status=status,
        finished_at=datetime.now(timezone.utc) if status == "success" else None,
        dataset_version_id=version.id,
        stats=stats or {"row_count": 2, "quality_score": 0.98},
    )
    input_row = PipelineRunInput(
        id=f"input-{pipeline_run_id}",
        pipeline_run_id=pipeline_run.id,
        dataset_version_id=version.id,
        input_ordinal=0,
        provenance=provenance
        or {
            "source_id": f"source-{dataset_id}",
            "source_type": "test_fixture",
            "locator": "fixture://snapshot",
            "content_hash": "b" * 64,
            "security_domain_id": DEFAULT_SECURITY_DOMAIN,
        },
    )
    db.add_all([pipeline, dataset, version, pipeline_run, input_row])
    return pipeline_run, version


@pytest.fixture
def completed_runs(db):
    first_run, first_version = _run(
        db,
        pipeline_id="pipeline-001",
        dataset_id="dataset-001",
        dataset_version_id="dv-001",
        pipeline_run_id="run-001",
        provenance={
            "source_id": "source-001",
            "source_type": "csv",
            "locator": "fixture://source-001",
            "content_hash": "1" * 64,
            "security_domain_id": DEFAULT_SECURITY_DOMAIN,
        },
    )
    second_run, second_version = _run(
        db,
        pipeline_id="pipeline-002",
        dataset_id="dataset-002",
        dataset_version_id="dv-002",
        pipeline_run_id="run-002",
        provenance={
            "source_id": "source-002",
            "source_type": "xlsx",
            "locator": "fixture://source-002",
            "content_hash": "2" * 64,
            "security_domain_id": DEFAULT_SECURITY_DOMAIN,
        },
    )
    db.commit()
    return {
        "runs": (first_run, second_run),
        "versions": (first_version, second_version),
    }


@pytest.fixture
def failed_run(db):
    run, version = _run(
        db,
        pipeline_id="pipeline-failed",
        dataset_id="dataset-failed",
        dataset_version_id="dv-failed",
        pipeline_run_id="run-failed",
        status="failed",
    )
    db.commit()
    return {"run": run, "version": version}
