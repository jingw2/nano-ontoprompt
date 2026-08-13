from datetime import datetime, timezone
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, String, UniqueConstraint, cast, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.database import Base


class CanonicalJSONB(UserDefinedType):
    """Bind canonical JSON text directly without a lossy Python float conversion."""

    cache_ok = True

    def get_col_spec(self, **kw):
        return "JSONB"

    def bind_expression(self, bindvalue):
        return cast(bindvalue, JSONB())

    def bind_processor(self, dialect):
        def process(value):
            if not isinstance(value, str):
                raise TypeError("manifest_projection must be canonical JSON text")
            return value
        return process


class OntologyRelease(Base):
    __tablename__ = "ontology_releases"
    __table_args__ = (
        UniqueConstraint("ontology_id", "version_no", name="uq_ontology_releases_ontology_version_no"),
        CheckConstraint("id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'", name="ck_ontology_releases_id_uuid"),
        CheckConstraint("version_no > 0", name="ck_ontology_releases_version_no"),
        CheckConstraint("octet_length(schema_hash) = 32", name="ck_ontology_releases_schema_hash_length"),
        CheckConstraint("digest(manifest_bytes, 'sha256') = schema_hash", name="ck_ontology_releases_manifest_integrity"),
        Index("ix_ontology_releases_schema_hash", "schema_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    manifest_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    manifest_projection: Mapped[str] = mapped_column(CanonicalJSONB(), nullable=False)
    schema_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=text("CURRENT_TIMESTAMP"))
