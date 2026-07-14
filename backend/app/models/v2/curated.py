import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Text, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class CuratedDataset(Base):
    __tablename__ = "v2_curated_datasets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_pipelines.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latest_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending_review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class CuratedReview(Base):
    __tablename__ = "v2_curated_reviews"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    curated_dataset_id: Mapped[str] = mapped_column(String, ForeignKey("v2_curated_datasets.id", ondelete="CASCADE"), nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|partial
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class CuratedRowEdit(Base):
    __tablename__ = "v2_curated_row_edits"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    review_id: Mapped[str] = mapped_column(String, ForeignKey("v2_curated_reviews.id", ondelete="CASCADE"), nullable=False)
    row_pk: Mapped[str] = mapped_column(String(200), nullable=False)
    field_name: Mapped[str] = mapped_column(String(200), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
