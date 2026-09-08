"""External MCP client schema-pin and OAuth-token tables (P7D external tools).

Both tables are 1:1 optional extensions of a tool_connection_versions row
whose provider kind is 'external_mcp' — see app/services/skills for the
analogous canonical-hash-recheck discipline this table's dispatch-time
quarantine mirrors."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class McpConnectionSchema(Base):
    __tablename__ = "mcp_connection_schemas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    connection_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tool_connection_versions.id", ondelete="RESTRICT"), nullable=False, unique=True)
    tool_schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tools: Mapped[list] = mapped_column(JSON, nullable=False)
    quarantined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quarantined_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pinned_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    pinned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class McpOauthToken(Base):
    __tablename__ = "mcp_oauth_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    connection_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tool_connection_versions.id", ondelete="RESTRICT"), nullable=False, unique=True)
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str] = mapped_column(String(20), nullable=False, default="Bearer")
    scope: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    audience: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    rotated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
