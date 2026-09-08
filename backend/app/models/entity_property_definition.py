"""Normalized ontology identity definitions and append-only migration findings.

`EntityPropertyDefinition` is the stable normalized projection of legacy
`Entity.properties` entries that carry explicit, validated schema metadata;
`OntologyMigrationFinding` is the append-only inventory record that stores the
source JSON hash/path and classification without ever mutating the source
payload.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
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
VALUE_TYPES = ("string", "number", "integer", "boolean", "date", "datetime", "object", "array")
SENSITIVITIES = ("public", "internal", "sensitive", "restricted")


class EntityPropertyDefinition(Base):
    __tablename__ = "entity_property_definitions"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_entity_property_definitions_id_uuid"),
        CheckConstraint(
            f"value_type IN ({', '.join(repr(value) for value in VALUE_TYPES)})",
            name="ck_entity_property_definitions_value_type",
        ),
        CheckConstraint(
            f"sensitivity IN ({', '.join(repr(value) for value in SENSITIVITIES)})",
            name="ck_entity_property_definitions_sensitivity",
        ),
        CheckConstraint(
            "jsonb_typeof(constraints) = 'object'",
            name="ck_entity_property_definitions_constraints_shape",
        ),
        CheckConstraint("ordinal >= 0", name="ck_entity_property_definitions_ordinal"),
        UniqueConstraint("entity_id", "normalized_key", name="uq_entity_property_definitions_entity_normalized_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False
    )
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="RESTRICT"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(200), nullable=False)
    display_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    value_type: Mapped[str] = mapped_column(String(40), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    constraints: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False, default="internal")
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deprecated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OntologyMigrationFinding(Base):
    __tablename__ = "ontology_migration_findings"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_ontology_migration_findings_id_uuid"),
        CheckConstraint(
            "octet_length(source_hash) = 32",
            name="ck_ontology_migration_findings_source_hash",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved')",
            name="ck_ontology_migration_findings_status",
        ),
        UniqueConstraint(
            "ontology_id", "kind", "item_id", "code",
            name="uq_ontology_migration_findings_scope",
        ),
        Index("ix_ontology_migration_findings_status", "ontology_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False
    )
    entity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("entities.id", ondelete="RESTRICT"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    item_id: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    classification: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )
