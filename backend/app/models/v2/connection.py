import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from sqlalchemy import String, DateTime, JSON, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class ConnectionKind(str, PyEnum):
    file = "file"
    mysql = "mysql"
    postgres = "postgres"
    mongo = "mongo"
    rest = "rest"

class ConnectionStatus(str, PyEnum):
    active = "active"
    inactive = "inactive"
    error = "error"

class Connection(Base):
    __tablename__ = "v2_connections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)  # ConnectionKind
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # 加密后的连接参数
    status: Mapped[str] = mapped_column(String(20), default="inactive")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
