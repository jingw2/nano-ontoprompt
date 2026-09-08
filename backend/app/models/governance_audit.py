"""Append-only governance audit authority: log, outbox, and chain head.

`GovernanceAuditLog` is append-only; `GovernanceAuditOutbox` carries
transaction-owned event payloads with a stable correlation ID; and
`GovernanceAuditChainHead` is the per-(security_domain, UTC date) partition
chain head locked `FOR UPDATE` during materialization so multi-worker forks are
impossible.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

UUID_CHECK = (
    "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$'"
)


class GovernanceAuditLog(Base):
    __tablename__ = "governance_audit_logs"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_governance_audit_logs_id_uuid"),
        CheckConstraint("sequence > 0", name="ck_governance_audit_logs_sequence"),
        CheckConstraint("octet_length(event_hash) = 32", name="ck_governance_audit_logs_event_hash"),
        UniqueConstraint("partition_key", "sequence", name="uq_governance_audit_logs_partition_sequence"),
        ForeignKeyConstraint(
            ["actor_user_id", "security_domain_id"],
            ["users.id", "users.security_domain_id"],
            ondelete="RESTRICT",
            name="fk_governance_audit_logs_actor_domain",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    security_domain_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False
    )
    partition_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    decision: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_ids: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    output_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    outcome: Mapped[str] = mapped_column(String(100), nullable=False)
    previous_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    event_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    release_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    connection_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    retention_class: Mapped[str] = mapped_column(String(30), nullable=False, default="standard")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class GovernanceAuditOutbox(Base):
    __tablename__ = "governance_audit_outbox"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_governance_audit_outbox_id_uuid"),
        CheckConstraint("state IN ('pending', 'materialized', 'failed')", name="ck_governance_audit_outbox_state"),
        CheckConstraint("attempts >= 0", name="ck_governance_audit_outbox_attempts"),
        UniqueConstraint("security_domain_id", "correlation_id", name="uq_governance_audit_outbox_domain_correlation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    security_domain_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )
    materialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GovernanceAuditChainHead(Base):
    __tablename__ = "governance_audit_chain_heads"
    __table_args__ = (
        CheckConstraint("next_sequence > 0", name="ck_governance_audit_chain_next_sequence"),
        CheckConstraint(
            "last_hash IS NULL OR octet_length(last_hash) = 32",
            name="ck_governance_audit_chain_last_hash",
        ),
    )

    partition_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    security_domain_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False
    )
    next_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    last_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )
