"""Immutable semantic snapshot and complete governed input associations."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class SemanticSnapshot(Base):
    """One immutable materialization of a published ontology release."""

    __tablename__ = "semantic_snapshots"
    __table_args__ = (
        CheckConstraint(
            "status IN ('materialized')",
            name="ck_semantic_snapshots_status",
        ),
        CheckConstraint(
            "length(materialization_hash) = 64",
            name="ck_semantic_snapshots_materialization_hash",
        ),
        CheckConstraint(
            "freshness_state IN ('fresh', 'stale', 'unknown')",
            name="ck_semantic_snapshots_freshness_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    ontology_release_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ontology_releases.id", ondelete="RESTRICT"), nullable=False,
    )
    quality_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    materialization_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Task 20: immutable source-freshness pins, frozen at materialization time.
    # A snapshot created without governed refresh context (the pre-Task-20
    # `materialize_snapshot` call shape) defaults to "unknown" — the policy
    # layer must never treat an unknown-provenance snapshot as silently fresh.
    freshness_state: Mapped[str] = mapped_column(
        String(10), nullable=False, default="unknown", server_default="unknown",
    )
    freshness_lag_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_cursor: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lineage_summary: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'"),
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="materialized", server_default="materialized")
    created_by: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now,
    )


class SemanticSnapshotInput(Base):
    """One complete snapshot-to-governed-pipeline-input association."""

    __tablename__ = "semantic_snapshot_inputs"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "dataset_version_id", "pipeline_run_id",
            name="uq_semantic_snapshot_inputs_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("semantic_snapshots.id", ondelete="RESTRICT"), nullable=False,
    )
    dataset_version_id: Mapped[str] = mapped_column(
        String, ForeignKey("v2_dataset_versions.id", ondelete="RESTRICT"), nullable=False,
    )
    pipeline_run_id: Mapped[str] = mapped_column(
        String, ForeignKey("v2_pipeline_runs.id", ondelete="RESTRICT"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now,
    )


def _raise_snapshot_mutation(*_args, **_kwargs):
    """Portable ORM guard for SQLite/MySQL paths without PostgreSQL triggers."""
    raise ValueError("SEMANTIC_SNAPSHOT_IMMUTABLE")


@event.listens_for(SemanticSnapshot, "before_insert")
def _validate_snapshot_hash(_mapper, _connection, target) -> None:
    """Keep ORM inserts portable while the migration adds a DB-level shape check."""
    if not re.fullmatch(r"[0-9a-f]{64}", target.materialization_hash or ""):
        raise ValueError("INVALID_MATERIALIZATION_HASH")


@event.listens_for(SemanticSnapshot, "before_insert")
def _validate_snapshot_release_published(_mapper, connection, target) -> None:
    """Reject snapshots pinned to a draft or revoked ontology release."""
    status = connection.execute(
        text("SELECT status FROM ontology_releases WHERE id = :ontology_release_id"),
        {"ontology_release_id": target.ontology_release_id},
    ).scalar_one_or_none()
    if status != "published":
        raise ValueError("SNAPSHOT_RELEASE_NOT_PUBLISHED")


@event.listens_for(SemanticSnapshotInput, "before_insert")
def _validate_snapshot_input(_mapper, connection, target) -> None:
    """Reject ungoverned inputs on ORM/database paths without trigger support."""
    run = connection.execute(
        text(
            "SELECT status, finished_at, dataset_version_id "
            "FROM v2_pipeline_runs WHERE id = :pipeline_run_id"
        ),
        {"pipeline_run_id": target.pipeline_run_id},
    ).mappings().one_or_none()
    has_lineage = connection.execute(
        text(
            "SELECT 1 FROM pipeline_run_inputs "
            "WHERE pipeline_run_id = :pipeline_run_id "
            "AND dataset_version_id = :dataset_version_id LIMIT 1"
        ),
        {
            "pipeline_run_id": target.pipeline_run_id,
            "dataset_version_id": target.dataset_version_id,
        },
    ).first() is not None
    if (
        run is None
        or run["status"] != "success"
        or run["finished_at"] is None
        or run["dataset_version_id"] is None
        or not has_lineage
    ):
        raise ValueError("SNAPSHOT_INPUT_NOT_GOVERNED")


event.listen(SemanticSnapshot, "before_update", _raise_snapshot_mutation)
event.listen(SemanticSnapshot, "before_delete", _raise_snapshot_mutation)
event.listen(SemanticSnapshotInput, "before_update", _raise_snapshot_mutation)
event.listen(SemanticSnapshotInput, "before_delete", _raise_snapshot_mutation)
