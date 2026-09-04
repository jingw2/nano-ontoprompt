"""Durable, application-owned evidence for a prepared business journey."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BusinessJourneyPreparation(Base):
    __tablename__ = "business_journey_preparations"
    __table_args__ = (UniqueConstraint("run_id", "journey_id", name="uq_business_journey_preparation_run"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(200), nullable=False)
    journey_id: Mapped[str] = mapped_column(String(100), nullable=False)
    ontology_id: Mapped[str] = mapped_column(String(36), ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False)
    ontology_release_id: Mapped[str] = mapped_column(String(36), ForeignKey("ontology_releases.id", ondelete="RESTRICT"), nullable=False)
    semantic_snapshot_id: Mapped[str] = mapped_column(String(36), ForeignKey("semantic_snapshots.id", ondelete="RESTRICT"), nullable=False)
    pipeline_run_id: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(String(200), nullable=False)
    curated_dataset_id: Mapped[str] = mapped_column(String(200), nullable=False)
    curated_review_id: Mapped[str] = mapped_column(String(200), nullable=False)
    model_config_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    mcp_descriptor_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    structured: Mapped[dict] = mapped_column(JSON, nullable=False)
    model_probe: Mapped[dict] = mapped_column(JSON, nullable=False)
    model_calls: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
