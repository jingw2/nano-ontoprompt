"""Agent derived-index outbox model (P4A-INDEX).

Consumed by the release-aware index service: every authoritative
instance/relation mutation emits exactly one outbox row via the 0006
PostgreSQL trigger, and the consumer marks rows applied after the
release-aware candidate index is updated.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class AgentIndexOutbox(Base):
    __tablename__ = "agent_index_outbox"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('upsert_instance', 'delete_instance', 'upsert_edge', 'delete_edge')",
            name="ck_agent_index_outbox_event_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    ontology_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    instance_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    edge_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    relation_definition_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
