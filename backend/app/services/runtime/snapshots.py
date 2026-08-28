"""Atomic creation and immutable reads of governed semantic snapshots."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ontology import OntologyProject
from app.models.ontology_release import OntologyRelease
from app.models.semantic_snapshot import SemanticSnapshot, SemanticSnapshotInput
from app.models.user import User
from app.schemas.runtime_snapshot import SnapshotView
from app.services.runtime.lineage import LineageError, collect_lineage


class SnapshotValidationError(Exception):
    """A release, lineage, provenance, or snapshot lookup is invalid."""

    def __init__(self, reason_code: str, message: str | None = None):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


SnapshotError = SnapshotValidationError


def _new_id() -> str:
    return str(uuid.uuid4())


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_materialization_hash(
    materialization_input: Mapping[str, Any] | None = None,
    *,
    ontology_release_id: str | None = None,
    input_pairs: Sequence[Sequence[str]] | None = None,
    quality_summary: Mapping[str, Any] | None = None,
    evidence_summary: Mapping[str, Any] | None = None,
) -> str:
    """Hash canonical UTF-8 JSON using the snapshot materialization contract.

    Callers may pass the already-built mapping or the four semantic fields;
    both forms use exactly the same canonicalization path.
    """
    if materialization_input is None:
        if ontology_release_id is None or input_pairs is None:
            raise TypeError("materialization input or canonical fields are required")
        materialization_input = canonical_materialization_input(
            ontology_release_id=ontology_release_id,
            input_pairs=input_pairs,
            quality_summary=quality_summary or {},
            evidence_summary=evidence_summary or {},
        )
    if not isinstance(materialization_input, Mapping):
        raise TypeError("materialization input must be a mapping")
    return hashlib.sha256(_canonical_json(dict(materialization_input)).encode("utf-8")).hexdigest()


def canonical_materialization_input(
    *,
    ontology_release_id: str,
    input_pairs: Sequence[Sequence[str]],
    quality_summary: Mapping[str, Any],
    evidence_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the canonical materialization object before hashing."""
    pairs = sorted({(str(pair[0]), str(pair[1])) for pair in input_pairs})
    return {
        "ontology_release_id": ontology_release_id,
        "input_pairs": [[dataset_version_id, pipeline_run_id] for dataset_version_id, pipeline_run_id in pairs],
        "quality_summary": dict(quality_summary),
        "evidence_summary": dict(evidence_summary),
    }


canonical_materialization_payload = canonical_materialization_input
compute_materialization_hash = canonical_materialization_hash
materialization_hash = canonical_materialization_hash


def _safe_error_code(exc: BaseException) -> str | None:
    message = str(exc)
    for code in (
        "SNAPSHOT_INPUT_NOT_GOVERNED",
        "INVALID_MATERIALIZATION_HASH",
        "SEMANTIC_SNAPSHOT_IMMUTABLE",
    ):
        if code in message:
            return code
    return None


def _synthetic_tenant(user_id: str | None) -> str | None:
    if not isinstance(user_id, str):
        return None
    parts = user_id.split("-")
    if len(parts) >= 3 and parts[0] == "user":
        return f"tenant-{parts[1]}"
    return None


def _validate_release_and_creator(
    db: Session,
    *,
    ontology_release_id: str,
    created_by: str,
) -> tuple[OntologyRelease, OntologyProject, User]:
    release = db.execute(
        select(OntologyRelease).where(OntologyRelease.id == ontology_release_id)
    ).scalar_one_or_none()
    if release is None:
        raise SnapshotValidationError("RELEASE_NOT_FOUND")
    if release.status != "published":
        raise SnapshotValidationError("RELEASE_NOT_PUBLISHED")

    project = db.execute(
        select(OntologyProject).where(OntologyProject.id == release.ontology_id)
    ).scalar_one_or_none()
    if project is None:
        raise SnapshotValidationError("RELEASE_PROVENANCE_MISSING")
    latest = project.latest_published_release_id
    if latest is not None and latest != release.id:
        raise SnapshotValidationError("RELEASE_DRIFT")

    creator = db.execute(select(User).where(User.id == created_by)).scalar_one_or_none()
    if creator is None:
        raise SnapshotValidationError("SNAPSHOT_CREATOR_NOT_FOUND")
    if project.security_domain_id and creator.security_domain_id != project.security_domain_id:
        raise SnapshotValidationError("TENANT_MISMATCH")
    return release, project, creator


def materialize_snapshot(
    db: Session,
    *,
    ontology_release_id: str,
    dataset_version_ids: Sequence[str],
    created_by: str,
) -> SemanticSnapshot:
    """Validate and atomically persist one immutable governed snapshot."""
    try:
        release, project, creator = _validate_release_and_creator(
            db,
            ontology_release_id=ontology_release_id,
            created_by=created_by,
        )
        lineage = collect_lineage(db, dataset_version_ids)
        if lineage.security_domain_ids and set(lineage.security_domain_ids) != {project.security_domain_id}:
            raise SnapshotValidationError("TENANT_MISMATCH")
        creator_tenant = _synthetic_tenant(creator.id)
        if lineage.tenant_ids and creator_tenant and set(lineage.tenant_ids) != {creator_tenant}:
            raise SnapshotValidationError("TENANT_MISMATCH")

        materialization_input = canonical_materialization_input(
            ontology_release_id=release.id,
            input_pairs=lineage.input_pairs,
            quality_summary=lineage.quality_summary,
            evidence_summary=lineage.evidence_summary,
        )
        materialization_hash = canonical_materialization_hash(materialization_input)
        snapshot = SemanticSnapshot(
            id=_new_id(),
            ontology_release_id=release.id,
            quality_summary=dict(lineage.quality_summary),
            evidence_summary=dict(lineage.evidence_summary),
            materialization_hash=materialization_hash,
            status="materialized",
            created_by=creator.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(snapshot)
        db.flush()
        for dataset_version_id, pipeline_run_id in lineage.input_pairs:
            db.add(SemanticSnapshotInput(
                id=_new_id(),
                snapshot_id=snapshot.id,
                dataset_version_id=dataset_version_id,
                pipeline_run_id=pipeline_run_id,
            ))
        # The Task 11 ORM/database guard runs on flush, so all rows above are
        # committed together or rolled back together.
        db.commit()
        db.refresh(snapshot)
    except SnapshotValidationError:
        db.rollback()
        raise
    except LineageError as exc:
        db.rollback()
        raise SnapshotValidationError(exc.reason_code, str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        reason = _safe_error_code(exc) or "SNAPSHOT_MATERIALIZATION_FAILED"
        raise SnapshotValidationError(reason, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        reason = _safe_error_code(exc) or "SNAPSHOT_MATERIALIZATION_FAILED"
        raise SnapshotValidationError(reason, str(exc)) from exc

    # Task 11 intentionally keeps the association table authoritative rather
    # than adding a lossy JSON list to the model.  These transient conveniences
    # preserve the public return shape without changing that schema.
    snapshot.dataset_version_ids = lineage.dataset_version_ids
    snapshot.pipeline_run_ids = lineage.pipeline_run_ids
    snapshot.input_pairs = lineage.input_pairs
    return snapshot


def get_snapshot(db: Session, snapshot_id: str) -> SnapshotView:
    """Return a detached immutable projection of one snapshot and its pins."""
    snapshot = db.execute(
        select(SemanticSnapshot).where(SemanticSnapshot.id == snapshot_id)
    ).scalar_one_or_none()
    if snapshot is None:
        raise SnapshotValidationError("SNAPSHOT_NOT_FOUND")
    input_rows = db.execute(
        select(SemanticSnapshotInput)
        .where(SemanticSnapshotInput.snapshot_id == snapshot.id)
        .order_by(SemanticSnapshotInput.dataset_version_id, SemanticSnapshotInput.pipeline_run_id)
    ).scalars().all()
    pairs = tuple((row.dataset_version_id, row.pipeline_run_id) for row in input_rows)
    return SnapshotView(
        id=snapshot.id,
        ontology_release_id=snapshot.ontology_release_id,
        dataset_version_ids=tuple(sorted({pair[0] for pair in pairs})),
        pipeline_run_ids=tuple(sorted({pair[1] for pair in pairs})),
        input_pairs=pairs,
        quality_summary=dict(snapshot.quality_summary or {}),
        evidence_summary=dict(snapshot.evidence_summary or {}),
        materialization_hash=snapshot.materialization_hash,
        status=snapshot.status,
        created_by=snapshot.created_by,
        created_at=snapshot.created_at,
    )


__all__ = [
    "SnapshotError",
    "SnapshotValidationError",
    "canonical_materialization_hash",
    "canonical_materialization_input",
    "canonical_materialization_payload",
    "compute_materialization_hash",
    "get_snapshot",
    "materialization_hash",
    "materialize_snapshot",
]
