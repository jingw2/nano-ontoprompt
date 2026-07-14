import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Text, ForeignKey
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

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id: Mapped[str] = mapped_column(String, ForeignKey("v2_pipelines.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending|running|success|failed|cancelled
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_dataset_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
