import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.models.security_domain import DEFAULT_SECURITY_DOMAIN_ID

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    security_domain_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("security_domains.id", ondelete="RESTRICT"),
        nullable=False,
        default=DEFAULT_SECURITY_DOMAIN_ID,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    @property
    def normalized_role(self) -> str:
        """Closed-ceiling role value; legacy/unknown roles map to viewer."""
        from app.services.authorization import normalize_role
        return normalize_role(self.role)
