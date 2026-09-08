from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


DEFAULT_SECURITY_DOMAIN_ID = "00000000-0000-0000-0000-000000000001"
UUID_CHECK = (
    "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$'"
)


class SecurityDomain(Base):
    __tablename__ = "security_domains"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_security_domains_id_uuid"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_security_domains_status"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=DEFAULT_SECURITY_DOMAIN_ID
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
