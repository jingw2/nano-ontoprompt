"""Agent configuration support tables (P2B-SCHEMA).

Application-state schema registries/versions (immutable, RESTRICT), tool
providers/connections/versions (approved only, no secrets), prompt-generation
provenance, and immutable Agent child rows (ontology bindings, external tool
bindings, retrieval sources).
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


class ApplicationStateSchemaRegistry(Base):
    __tablename__ = "application_state_schema_registries"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_assr_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    application_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    active_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("application_state_schema_versions.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ApplicationStateSchemaVersion(Base):
    __tablename__ = "application_state_schema_versions"
    __table_args__ = (
        UniqueConstraint("registry_id", "version_no", name="uq_assv_registry_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    registry_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("application_state_schema_registries.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    json_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ToolProvider(Base):
    __tablename__ = "tool_providers"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_tool_providers_status"),
        CheckConstraint(
            "kind IN ('search', 'playwright', 'skill', 'external_mcp', 'ontology_mcp')",
            name="ck_tool_providers_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ToolConnection(Base):
    __tablename__ = "tool_connections"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled', 'revoked')", name="ck_tool_connections_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tool_providers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    active_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tool_connection_versions.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ToolConnectionVersion(Base):
    __tablename__ = "tool_connection_versions"
    __table_args__ = (
        UniqueConstraint("connection_id", "version_no", name="uq_tcv_connection_version"),
        CheckConstraint(
            "approval_status IN ('approved', 'pending', 'rejected')", name="ck_tcv_approval"
        ),
        CheckConstraint(
            "health_status IN ('healthy', 'unhealthy', 'unknown')", name="ck_tcv_health"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tool_connections.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audience: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    credential_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    allowlists: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    approval_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    health_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PromptGeneration(Base):
    __tablename__ = "prompt_generations"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="ck_pg_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    base_version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    model_config_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model_config_versions.id", ondelete="RESTRICT"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    requester_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentOntologyBinding(Base):
    __tablename__ = "agent_ontology_bindings"
    __table_args__ = (
        # one Agent (version) binds at most one published Ontology
        UniqueConstraint("agent_version_id", name="uq_agent_ontology_binding_single"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    ontology_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    allowlists: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # tool categories enabled for this bound ontology (MCP/query/write/logic/
    # action); NULL = legacy binding filtered by selected_tools only
    enabled_categories: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentExternalToolBinding(Base):
    __tablename__ = "agent_external_tool_bindings"
    __table_args__ = (
        UniqueConstraint("agent_version_id", "alias", name="uq_aetb_version_alias"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tool_connection_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tool_connection_versions.id", ondelete="RESTRICT"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AgentRetrievalSource(Base):
    __tablename__ = "agent_retrieval_sources"
    __table_args__ = (
        UniqueConstraint("agent_version_id", "source_id", name="uq_ars_version_source"),
        CheckConstraint(
            "kind IN ('fixed', 'semantic', 'document', 'function')", name="ck_ars_kind"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    applicability_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
