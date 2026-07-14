"""PRD v1.1 Ontology Logic & State Machine 模型"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Text, Boolean, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class OntologyLogicRule(Base):
    """逻辑规则 — 六大类型: validation/mapping/inference/state/security/automation"""
    __tablename__ = "v2_ontology_logic_rules"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    logic_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_entity_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expression: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                                                 onupdate=lambda: datetime.now(timezone.utc))


class OntologyStateMachine(Base):
    """状态机 — 控制实体状态流转"""
    __tablename__ = "v2_ontology_state_machines"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    entity_type_name: Mapped[str] = mapped_column(String(200), nullable=False)
    state_property: Mapped[str] = mapped_column(String(100), nullable=False)
    states: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    transitions: Mapped[dict] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                                                 onupdate=lambda: datetime.now(timezone.utc))
