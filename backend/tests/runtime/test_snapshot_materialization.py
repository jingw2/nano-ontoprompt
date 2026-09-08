"""Task 12 tests for complete governed lineage and immutable snapshots."""

from __future__ import annotations

import hashlib
import json

import pytest

from app.models.ontology import OntologyProject
from app.models.ontology_release import OntologyRelease
from app.models.semantic_snapshot import SemanticSnapshotInput
from app.models.v2.pipeline import PipelineRunInput
from app.services.runtime.lineage import collect_lineage
from app.services.runtime.snapshots import (
    SnapshotValidationError,
    canonical_materialization_hash,
    get_snapshot,
    materialize_snapshot,
)


def test_collect_lineage_returns_sorted_deduplicated_ids_and_safe_summaries(db, completed_runs):
    bundle = collect_lineage(db, ["dv-002", "dv-001", "dv-001"])

    assert bundle.dataset_version_ids == ("dv-001", "dv-002")
    assert bundle.pipeline_run_ids == ("run-001", "run-002")
    assert bundle.input_pairs == (
        ("dv-001", "run-001"),
        ("dv-002", "run-002"),
    )
    assert bundle.quality_summary["row_count"] == 4
    assert {item["source_id"] for item in bundle.evidence_summary["citations"]} == {
        "source-001",
        "source-002",
    }


def test_materialize_snapshot_binds_all_inputs_and_is_order_independent(
    db, valid_release, completed_runs,
):
    first = materialize_snapshot(
        db,
        ontology_release_id=valid_release.id,
        dataset_version_ids=["dv-002", "dv-001"],
        created_by="user-001",
    )
    second = materialize_snapshot(
        db,
        ontology_release_id=valid_release.id,
        dataset_version_ids=["dv-001", "dv-002"],
        created_by="user-001",
    )

    assert first.materialization_hash == second.materialization_hash
    assert set(first.pipeline_run_ids) == {"run-001", "run-002"}
    assert db.query(SemanticSnapshotInput).count() == 4


def test_materialize_snapshot_rejects_failed_run_or_unpublished_release(
    db, valid_release, draft_release, completed_runs, failed_run,
):
    with pytest.raises(SnapshotValidationError) as release_error:
        materialize_snapshot(
            db,
            ontology_release_id=draft_release.id,
            dataset_version_ids=["dv-001"],
            created_by="user-001",
        )
    assert release_error.value.reason_code == "RELEASE_NOT_PUBLISHED"

    with pytest.raises(SnapshotValidationError) as lineage_error:
        materialize_snapshot(
            db,
            ontology_release_id=valid_release.id,
            dataset_version_ids=["dv-failed"],
            created_by="user-001",
        )
    assert lineage_error.value.reason_code == "LINEAGE_NOT_GOVERNED"
    assert db.query(SemanticSnapshotInput).count() == 0


def test_materialize_snapshot_rejects_incomplete_lineage_atomically(
    db, valid_release, completed_runs,
):
    with pytest.raises(SnapshotValidationError) as error:
        materialize_snapshot(
            db,
            ontology_release_id=valid_release.id,
            dataset_version_ids=["dv-001", "missing-dv"],
            created_by="user-001",
        )

    assert error.value.reason_code == "LINEAGE_INCOMPLETE"
    assert db.query(SemanticSnapshotInput).count() == 0


def test_materialize_snapshot_rejects_release_that_is_no_longer_the_latest_published(
    db, valid_release, completed_runs,
):
    """A release that was once published but has since been superseded by a
    newer published release for the same ontology must never be pinned by a
    new snapshot — even though its own `status` is still `published`."""
    project = db.get(OntologyProject, valid_release.ontology_id)
    project.latest_published_release_id = "release-superseded-999"
    db.commit()

    with pytest.raises(SnapshotValidationError) as error:
        materialize_snapshot(
            db,
            ontology_release_id=valid_release.id,
            dataset_version_ids=["dv-001"],
            created_by="user-001",
        )
    assert error.value.reason_code == "RELEASE_DRIFT"
    assert db.query(SemanticSnapshotInput).count() == 0


def test_materialize_snapshot_rejects_cross_security_domain_provenance(
    db, valid_release, completed_runs,
):
    db.query(PipelineRunInput).filter(
        PipelineRunInput.pipeline_run_id == "run-001",
    ).one().provenance = {
        "source_id": "source-foreign",
        "source_type": "csv",
        "locator": "fixture://foreign",
        "content_hash": "f" * 64,
        "security_domain_id": "foreign-security-domain",
    }
    db.commit()

    with pytest.raises(SnapshotValidationError) as error:
        materialize_snapshot(
            db,
            ontology_release_id=valid_release.id,
            dataset_version_ids=["dv-001"],
            created_by="user-001",
        )
    assert error.value.reason_code == "TENANT_MISMATCH"
    assert db.query(SemanticSnapshotInput).count() == 0


def test_materialize_snapshot_defaults_freshness_to_unknown_without_refresh_context(
    db, valid_release, completed_runs,
):
    """Task 20: a snapshot materialized through the plain (pre-Task-20)
    `materialize_snapshot` call shape — no refresh context — must default
    to `freshness_state="unknown"`, never a silently "fresh" pin."""
    snapshot = materialize_snapshot(
        db,
        ontology_release_id=valid_release.id,
        dataset_version_ids=["dv-001"],
        created_by="user-001",
    )

    assert snapshot.freshness_state == "unknown"
    assert snapshot.freshness_lag_seconds is None
    assert snapshot.source_cursor is None

    view = get_snapshot(db, snapshot.id)
    assert view.freshness_state == "unknown"
    assert view.source_cursor is None


def test_get_snapshot_returns_immutable_pins_and_provenance(db, valid_release, completed_runs):
    snapshot = materialize_snapshot(
        db,
        ontology_release_id=valid_release.id,
        dataset_version_ids=["dv-001", "dv-002"],
        created_by="user-001",
    )

    view = get_snapshot(db, snapshot.id)

    assert view.id == snapshot.id
    assert view.ontology_release_id == valid_release.id
    assert view.dataset_version_ids == ("dv-001", "dv-002")
    assert view.pipeline_run_ids == ("run-001", "run-002")
    assert view.materialization_hash == snapshot.materialization_hash
    with pytest.raises((AttributeError, TypeError, ValueError)):
        view.dataset_version_ids += ("dv-003",)


def test_canonical_materialization_hash_is_compact_sorted_utf8():
    payload = {
        "evidence_summary": {"citations": [{"source_id": "来源-一"}]},
        "quality_summary": {"row_count": 2},
        "input_pairs": [["dv-001", "run-001"]],
        "ontology_release_id": "release-一",
    }
    expected_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    assert canonical_materialization_hash(payload) == hashlib.sha256(expected_bytes).hexdigest()
    assert canonical_materialization_hash(dict(reversed(list(payload.items())))) == canonical_materialization_hash(payload)


def test_lineage_summary_never_contains_protected_row_contents(db, valid_release, completed_runs):
    run = completed_runs["runs"][0]
    run.stats = {
        "row_count": 2,
        "raw_rows": [{"customer_name": "PROTECTED-ROW-VALUE"}],
    }
    db.commit()

    bundle = collect_lineage(db, ["dv-001"])
    serialized = json.dumps(
        {"quality": bundle.quality_summary, "evidence": bundle.evidence_summary},
        ensure_ascii=False,
    )
    assert "PROTECTED-ROW-VALUE" not in serialized
