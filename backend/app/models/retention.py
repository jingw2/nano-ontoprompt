"""Retention governance ORM shapes (P6A). Schema-registration only — all
retention application logic uses raw SQL (see app/services/retention/)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Index, JSON,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_retention_policies_status"),
        UniqueConstraint("security_domain_id", name="uq_retention_policies_domain"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    security_domain_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    active_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("retention_policy_versions.id", ondelete="RESTRICT"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RetentionPolicyVersion(Base):
    __tablename__ = "retention_policy_versions"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'active', 'superseded')", name="ck_rpv_status"),
        UniqueConstraint("policy_id", "version_no", name="uq_rpv_policy_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    policy_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("retention_policies.id", ondelete="RESTRICT"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rules: Mapped[dict] = mapped_column(JSON, nullable=False)
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RetentionHold(Base):
    __tablename__ = "retention_holds"
    __table_args__ = (
        CheckConstraint("scope_type IN ('subject', 'session', 'turn', 'object')", name="ck_rh_scope_type"),
        Index("ix_rh_active_scope", "security_domain_id", "scope_type", "scope_id",
              postgresql_where=Text("released_at IS NULL")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    security_domain_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    issued_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    released_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RetentionEpoch(Base):
    __tablename__ = "retention_epochs"

    security_domain_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("security_domains.id", ondelete="RESTRICT"), primary_key=True)
    epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
