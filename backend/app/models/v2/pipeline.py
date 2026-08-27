import uuid
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, String, DateTime, JSON, Text, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class Pipeline(Base):
    __tablename__ = "v2_pipelines"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(100), nullable=True, default="通用")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    source_dataset_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_datasets.id"), nullable=True)
    route: Mapped[str | None] = mapped_column(String(1), nullable=True)  # A|B|C (legacy, inferred from definition)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # legacy steps format
    definition: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # new DSL: {nodes: [...], edges: [...]}
    target_curated_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    schedule_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|editing|running|failed|published
    branch: Mapped[str | None] = mapped_column(String(50), nullable=True, default="main")
    version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class PipelineVersion(Base):
    __tablename__ = "v2_pipeline_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id: Mapped[str] = mapped_column(String, ForeignKey("v2_pipelines.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    definition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PipelineRun(Base):
    __tablename__ = "v2_pipeline_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'cancelled')",
            name="ck_v2_pipeline_runs_status",
        ),
        CheckConstraint(
            "status <> 'success' OR (finished_at IS NOT NULL AND dataset_version_id IS NOT NULL)",
            name="ck_v2_pipeline_runs_success_completion",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id: Mapped[str] = mapped_column(String, ForeignKey("v2_pipelines.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending|running|success|failed|cancelled
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_dataset_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @property
    def is_governed(self) -> bool:
        """Whether this run has a completed, durable output for snapshot use."""
        return (
            self.status == "success"
            and self.finished_at is not None
            and self.dataset_version_id is not None
        )


class PipelineRunInput(Base):
    """Authoritative multi-source input lineage for a PipelineRun (Task 6).

    `PipelineRun.dataset_version_id` above remains a backwards-compatible
    primary/output pointer and is never the complete lineage set; every
    input DatasetVersion a run consumed is recorded here instead. Table name
    deliberately unprefixed (no `v2_`) — see app/models/v2/refresh.py's
    module docstring for why. `pipeline_run_id`/`dataset_version_id` are
    intentionally not hard foreign keys: `record_refresh_outcome` persists
    whatever ids a governed connector/orchestrator hands it, and a
    rolled-back outcome must never be blocked by (or create) those rows.
    """

    __tablename__ = "pipeline_run_inputs"
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", "input_ordinal", name="uq_pipeline_run_inputs_ordinal"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_run_id: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_cursor: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    input_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
