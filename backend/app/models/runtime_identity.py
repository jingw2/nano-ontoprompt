"""Delegated runtime credentials (Task 13, RFC 8693 OAuth 2.0 Token Exchange
semantics): a short-lived, signed JWT binds a registered Agent/service
identity (an `OAuthClient`, the `act` claim) to the user it acts on behalf of
(the `sub` claim), scoped to one audience and TTL. Mirrors
`oauth_authorization_codes`/`oauth_refresh_tokens`: the plaintext token is
never persisted, only a SHA-256 hash plus revocation metadata, so a database
read alone can never reconstruct a bearer credential.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

UUID_CHECK = (
    "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$'"
)


class RuntimeDelegatedCredential(Base):
    __tablename__ = "runtime_delegated_credentials"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_runtime_delegated_credentials_id_uuid"),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_runtime_delegated_credentials_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    client_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    audience: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
