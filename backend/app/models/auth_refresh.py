import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

UUID_CHECK = (
    "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$'"
)


class AuthRefreshFamily(Base):
    __tablename__ = "auth_refresh_families"
    __table_args__ = (
        UniqueConstraint("id", "security_domain_id", name="uq_auth_refresh_family_domain"),
        ForeignKeyConstraint(
            ["user_id", "security_domain_id"],
            ["users.id", "users.security_domain_id"],
            ondelete="RESTRICT",
            name="fk_auth_refresh_family_user_domain",
        ),
        CheckConstraint(UUID_CHECK, name="ck_auth_refresh_families_id_uuid"),
        CheckConstraint("current_generation >= 0", name="ck_auth_refresh_family_generation"),
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_auth_refresh_family_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    security_domain_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False
    )
    current_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthRefreshToken(Base):
    __tablename__ = "auth_refresh_tokens"
    __table_args__ = (
        UniqueConstraint("family_id", "generation", name="uq_auth_refresh_token_generation"),
        CheckConstraint(UUID_CHECK, name="ck_auth_refresh_tokens_id_uuid"),
        CheckConstraint("generation >= 0", name="ck_auth_refresh_token_generation"),
        CheckConstraint(
            "status IN ('active', 'used', 'revoked', 'expired')",
            name="ck_auth_refresh_token_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    family_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("auth_refresh_families.id", ondelete="RESTRICT"), nullable=False
    )
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
