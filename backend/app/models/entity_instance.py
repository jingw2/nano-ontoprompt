import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class EntityInstance(Base):
    """行级实例数据 — Pipeline Mapping 每行数据存为一条 instance"""
    __tablename__ = "entity_instances"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id: Mapped[str] = mapped_column(String, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    row_identity: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    row_data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
