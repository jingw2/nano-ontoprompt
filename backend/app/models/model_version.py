"""Immutable LLM model behavior versioning (P2A-MODEL).

`ModelConfigVersion` pins one exact immutable behavior contract (provider, API
base, non-secret options, behavior hash and a model-contract array); legacy
migrations leave the verified-window fields unverified (NULL) rather than
guessing a tokenizer/window.  `ModelCredential` holds the encrypted secret
binding with rotation/revocation ledger.  `ModelMigrationFinding` is the
append-only evidence trail for blocked/remediated/archived identities.
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


class ModelConfigVersion(Base):
    __tablename__ = "model_config_versions"
    __table_args__ = (
        UniqueConstraint(
            "model_config_id", "version_no", name="uq_model_config_versions_config_version"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    model_config_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("model_configs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    api_base: Mapped[str | None] = mapped_column(String(500), nullable=True)
    options: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    behavior_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_contract: Mapped[list] = mapped_column(JSON, nullable=False)
    conservative_input_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ModelCredential(Base):
    __tablename__ = "model_credentials"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked')", name="ck_model_credentials_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    model_config_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("model_configs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    secret_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ModelMigrationFinding(Base):
    __tablename__ = "model_migration_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    model_config_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("model_configs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    field: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
