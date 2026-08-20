import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class McpWriteRequest(Base):
    __tablename__ = "mcp_write_requests"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_mcp_write_requests_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    oauth_client_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    ontology_id: Mapped[str] = mapped_column(
        String, ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False
    )
    release_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ontology_releases.id", ondelete="RESTRICT"), nullable=False
    )
    descriptor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    target_instance_id: Mapped[str | None] = mapped_column(String, nullable=True)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_canonical: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
