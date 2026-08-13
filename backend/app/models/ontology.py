import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models.security_domain import DEFAULT_SECURITY_DOMAIN_ID

class OntologyProject(Base):
    __tablename__ = "ontology_projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(20), default="v0.1")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    build_mode: Mapped[str] = mapped_column(String(30), default="simple_llm", nullable=True)
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    security_domain_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("security_domains.id", ondelete="RESTRICT"),
        nullable=False,
        default=DEFAULT_SECURITY_DOMAIN_ID,
    )
    latest_published_release_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("ontology_releases.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
