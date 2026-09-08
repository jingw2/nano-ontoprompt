"""Ontology data grant (P2B-DATAGRANT).

Strictly runtime/data-plane authority: principal, ontology, closed data
capabilities, entity/property/relation/action allowlists, policy version,
validity window and a restricted-DSL row policy.  Revisions are immutable;
create/revise/revoke are audited CAS operations with no hard delete.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    JSON,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OntologyDataGrant(Base):
    __tablename__ = "ontology_data_grants"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_ontology_data_grants_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False)
    entity_allowlist: Mapped[list | None] = mapped_column(JSON, nullable=True)
    property_allowlist: Mapped[list | None] = mapped_column(JSON, nullable=True)
    relation_allowlist: Mapped[list | None] = mapped_column(JSON, nullable=True)
    action_allowlist: Mapped[list | None] = mapped_column(JSON, nullable=True)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False, default="restricted-policy-dsl-v1")
    row_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    revoked_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
