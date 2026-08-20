import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey,
    ForeignKeyConstraint, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

UUID_CHECK = (
    "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$'"
)


class OAuthClient(Base):
    __tablename__ = "oauth_clients"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_oauth_clients_id_uuid"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    redirect_uris: Mapped[list] = mapped_column(JSON, nullable=False)
    allowed_scopes: Mapped[list] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class OAuthAuthorizationCode(Base):
    __tablename__ = "oauth_authorization_codes"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_oauth_auth_codes_id_uuid"),
        CheckConstraint("code_challenge_method IN ('S256')", name="ck_oauth_auth_codes_method"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    client_id: Mapped[str] = mapped_column(String(36), ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(10), nullable=False)
    scope: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class OAuthRefreshFamily(Base):
    __tablename__ = "oauth_refresh_families"
    __table_args__ = (
        UniqueConstraint("id", "security_domain_id", name="uq_oauth_refresh_family_domain"),
        ForeignKeyConstraint(
            ["user_id", "security_domain_id"],
            ["users.id", "users.security_domain_id"],
            ondelete="RESTRICT",
            name="fk_oauth_refresh_family_user_domain",
        ),
        CheckConstraint(UUID_CHECK, name="ck_oauth_refresh_families_id_uuid"),
        CheckConstraint("current_generation >= 0", name="ck_oauth_refresh_family_generation"),
        CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_oauth_refresh_family_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id: Mapped[str] = mapped_column(String(36), ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    security_domain_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    current_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class OAuthRefreshToken(Base):
    __tablename__ = "oauth_refresh_tokens"
    __table_args__ = (
        UniqueConstraint("family_id", "generation", name="uq_oauth_refresh_token_generation"),
        CheckConstraint(UUID_CHECK, name="ck_oauth_refresh_tokens_id_uuid"),
        CheckConstraint("generation >= 0", name="ck_oauth_refresh_token_generation"),
        CheckConstraint("status IN ('active', 'used', 'revoked', 'expired')", name="ck_oauth_refresh_token_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    family_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("oauth_refresh_families.id", ondelete="RESTRICT"), nullable=False
    )
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
