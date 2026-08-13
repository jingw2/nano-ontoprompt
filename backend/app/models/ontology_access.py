"""OntologyProjectAccessGrant: the sole design/lifecycle authority for an Ontology.

The grant has a closed capability vocabulary (`discover|read|edit|publish`),
a monotonic revision, `active|revoked|expired` status, a validity interval,
creator/revoker, audited `base_revision` create/revise/revoke, and no hard
delete.  Capabilities never expand the role ceiling and no actor may
self-escalate.  The table and its creator-grant backfill are created by the
0003 `upgrade_access_foundation()` migration helper.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
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
CAPABILITIES_JSON = '["discover", "read", "edit", "publish"]'


class OntologyProjectAccessGrant(Base):
    __tablename__ = "ontology_project_access_grants"
    __table_args__ = (
        CheckConstraint(UUID_CHECK, name="ck_ontology_project_access_grants_id_uuid"),
        CheckConstraint("revision > 0", name="ck_ontology_project_access_grants_revision"),
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_ontology_project_access_grants_status",
        ),
        CheckConstraint(
            f"jsonb_typeof(capabilities) = 'array' AND capabilities <@ '{CAPABILITIES_JSON}'::jsonb",
            name="ck_ontology_project_access_grants_capabilities",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_from < valid_until",
            name="ck_ontology_project_access_grants_validity",
        ),
        UniqueConstraint("ontology_id", "user_id", name="uq_ontology_project_access_grant_slot"),
        ForeignKeyConstraint(
            ["user_id", "security_domain_id"],
            ["users.id", "users.security_domain_id"],
            ondelete="RESTRICT",
            name="fk_ontology_project_access_grant_user_domain",
        ),
        ForeignKeyConstraint(
            ["ontology_id", "security_domain_id"],
            ["ontology_projects.id", "ontology_projects.security_domain_id"],
            ondelete="RESTRICT",
            name="fk_ontology_project_access_grant_ontology_domain",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    security_domain_id: Mapped[str] = mapped_column(String(36), nullable=False)
    capabilities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    revoked_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
