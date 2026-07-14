"""PRD v1.1 Ontology Action Type & Action Run 模型"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Text, Boolean, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class OntologyActionType(Base):
    """动作类型 — 可执行业务操作"""
    __tablename__ = "v2_ontology_action_types"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_entity_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    action_category: Mapped[str] = mapped_column(String(50), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    submission_criteria: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    effects: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    side_effects: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    permission_rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    backed_by_function: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                                                 onupdate=lambda: datetime.now(timezone.utc))


class OntologyActionRun(Base):
    """动作执行记录 — 审计与回滚"""
    __tablename__ = "v2_ontology_action_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    action_type_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_ontology_action_types.id"), nullable=True)
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    target_object_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    before_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    side_effect_results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
