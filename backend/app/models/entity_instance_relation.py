"""Entity instance relations (P3A-INSTANCE).

Authoritative business-object relationships: ontology, source/target instance
FKs (RESTRICT), a release-defined relation type, properties, revision and a
soft-delete timestamp.  The active edge (non-deleted) is unique per
(source, target, relation_definition).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EntityInstanceRelation(Base):
    __tablename__ = "entity_instance_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_instance_id", "target_instance_id", "relation_definition_id",
            name="uq_entity_instance_relations_active_edge",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_instance_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entity_instances.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    target_instance_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entity_instances.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    relation_definition_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("relations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    properties: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
