"""Agent identity + immutable version + access grants (P2B-SCHEMA).

`Agent` is a stable identity with `visibility=private|restricted`, status,
owner, active-version pointer and soft-delete fields; behavior-facing name and
description live only in the immutable `AgentVersion`.  `AgentAccessGrant`
carries the closed `discover|run|view_config|edit|view_audit` capability set
with monotonic revision and no hard delete.
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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint("visibility IN ('private', 'restricted')", name="ck_agents_visibility"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_agents_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    visibility: Mapped[str] = mapped_column(String(12), nullable=False, default="private")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    active_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version_no", name="uq_agent_versions_agent_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_model_config_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model_config_versions.id", ondelete="RESTRICT"), nullable=False
    )
    default_model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    application_state_schema_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("application_state_schema_versions.id", ondelete="RESTRICT"), nullable=False
    )
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_generation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("prompt_generations.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentAccessGrant(Base):
    __tablename__ = "agent_access_grants"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')", name="ck_agent_access_grants_status"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False)  # discover|run|view_config|edit|view_audit
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    revoked_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
