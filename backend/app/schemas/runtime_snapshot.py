"""Transport-neutral contracts for immutable semantic snapshots.

The snapshot service deliberately exposes IDs, aggregate quality information,
and source citations only.  Dataset row values are never part of these
contracts or of a materialization hash.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LineageBundle(BaseModel):
    """The complete, deterministic lineage projection for selected inputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_version_ids: tuple[str, ...] = ()
    pipeline_run_ids: tuple[str, ...] = ()
    input_pairs: tuple[tuple[str, str], ...] = ()
    security_domain_ids: tuple[str, ...] = ()
    tenant_ids: tuple[str, ...] = ()
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)


class SnapshotView(BaseModel):
    """Immutable read projection of a materialized semantic snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    ontology_release_id: str
    dataset_version_ids: tuple[str, ...] = ()
    pipeline_run_ids: tuple[str, ...] = ()
    input_pairs: tuple[tuple[str, str], ...] = ()
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    materialization_hash: str
    status: str
    created_by: str
    created_at: datetime | None = None

    @property
    def snapshot_id(self) -> str:
        """Compatibility alias used by Runtime callers."""
        return self.id

    @property
    def release_id(self) -> str:
        """Compatibility alias for the pinned ontology release."""
        return self.ontology_release_id
