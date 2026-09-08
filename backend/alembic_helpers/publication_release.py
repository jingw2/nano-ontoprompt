"""Alembic producer for the immutable OntologyRelease contract."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade_release_foundation() -> None:
    op.create_table(
        "ontology_releases",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("ontology_id", sa.String(), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("manifest_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("manifest_projection", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_hash", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_ontology_releases"),
        sa.UniqueConstraint("ontology_id", "version_no", name="uq_ontology_releases_ontology_version_no"),
        sa.CheckConstraint("id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'", name="ck_ontology_releases_id_uuid"),
        sa.CheckConstraint("version_no > 0", name="ck_ontology_releases_version_no"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="RESTRICT", name="fk_ontology_releases_ontology"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT", name="fk_ontology_releases_creator"),
        sa.CheckConstraint("octet_length(schema_hash) = 32", name="ck_ontology_releases_schema_hash_length"),
        sa.CheckConstraint("digest(manifest_bytes, 'sha256') = schema_hash", name="ck_ontology_releases_manifest_integrity"),
    )
    op.create_index("ix_ontology_releases_schema_hash", "ontology_releases", ["schema_hash"], unique=False)
    op.execute(
        """
        CREATE FUNCTION validate_ontology_release_domain() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE project_domain varchar(36); creator_domain varchar(36);
        BEGIN
          EXECUTE format('SELECT security_domain_id FROM %I.ontology_projects WHERE id=$1', TG_TABLE_SCHEMA)
            INTO project_domain USING NEW.ontology_id;
          EXECUTE format('SELECT security_domain_id FROM %I.users WHERE id=$1', TG_TABLE_SCHEMA)
            INTO creator_domain USING NEW.created_by;
          IF project_domain IS NULL OR creator_domain IS NULL OR project_domain <> creator_domain THEN
            RAISE EXCEPTION 'SECURITY_DOMAIN_MISMATCH';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER ontology_releases_validate_domain BEFORE INSERT OR UPDATE ON ontology_releases "
        "FOR EACH ROW EXECUTE FUNCTION validate_ontology_release_domain()"
    )
    op.execute(
        """
        CREATE FUNCTION reject_ontology_release_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'RELEASE_IMMUTABLE';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER ontology_releases_immutable BEFORE UPDATE OR DELETE ON ontology_releases "
        "FOR EACH ROW EXECUTE FUNCTION reject_ontology_release_mutation()"
    )
    op.add_column("ontology_projects", sa.Column("latest_published_release_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_ontology_projects_latest_release",
        "ontology_projects",
        "ontology_releases",
        ["latest_published_release_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade_release_foundation() -> None:
    op.drop_constraint("fk_ontology_projects_latest_release", "ontology_projects", type_="foreignkey")
    op.drop_column("ontology_projects", "latest_published_release_id")
    op.execute("DROP TRIGGER IF EXISTS ontology_releases_immutable ON ontology_releases")
    op.execute("DROP FUNCTION IF EXISTS reject_ontology_release_mutation()")
    op.execute("DROP TRIGGER IF EXISTS ontology_releases_validate_domain ON ontology_releases")
    op.execute("DROP FUNCTION IF EXISTS validate_ontology_release_domain()")
    op.drop_index("ix_ontology_releases_schema_hash", table_name="ontology_releases")
    op.drop_table("ontology_releases")
