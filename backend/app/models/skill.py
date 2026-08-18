"""Signed Skill support tables (P7C external tools).

Immutable signed manifests: a Skill version's manifest is canonicalized
(sort_keys, compact separators) and hashed; signatures are Ed25519 over the
hash. Versions carry an approval gate; bindings mirror the external-tool
binding pattern."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, JSON, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SkillPackage(Base):
    __tablename__ = "skill_packages"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_skill_packages_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("package_id", "version_no", name="uq_skill_versions_package_no"),
        CheckConstraint("approval_status IN ('pending', 'approved', 'rejected')", name="ck_skill_versions_approval"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    package_id: Mapped[str] = mapped_column(String(36), ForeignKey("skill_packages.id", ondelete="RESTRICT"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    approved_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SkillSignature(Base):
    __tablename__ = "skill_signatures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    version_id: Mapped[str] = mapped_column(String(36), ForeignKey("skill_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    algorithm: Mapped[str] = mapped_column(String(20), nullable=False, default="ed25519")
    public_key_hex: Mapped[str] = mapped_column(String(128), nullable=False)
    signature_hex: Mapped[str] = mapped_column(String(256), nullable=False)
    signer_identity: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentSkillBinding(Base):
    __tablename__ = "agent_skill_bindings"
    __table_args__ = (
        UniqueConstraint("agent_version_id", "alias", name="uq_asb_version_alias"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    skill_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("skill_versions.id", ondelete="RESTRICT"), nullable=False)
    alias: Mapped[str] = mapped_column(String(55), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
