"""Hidden publication governance foundation.

Revision ID: 0003_publication_governance
Revises: 0002_entity_identifiers
"""

from alembic import op
import sqlalchemy as sa

from alembic_helpers.publication_release import (
    downgrade_release_foundation,
    upgrade_release_foundation,
)
from app.services.governance_audit import (
    downgrade_audit_foundation,
    upgrade_audit_foundation,
)
from app.services.publication.preflight import (
    downgrade_identity_foundation,
    upgrade_identity_foundation,
)
from app.services.ontology_access import (
    downgrade_access_foundation,
    upgrade_access_foundation,
)
from app.services.publication.cutover import (
    downgrade_cutover_guards,
    upgrade_cutover_guards,
)


revision = "0003_publication_governance"
down_revision = "0002_entity_identifiers"
branch_labels = None
depends_on = None

DEFAULT_SECURITY_DOMAIN_ID = "00000000-0000-0000-0000-000000000001"
UUID_CHECK = (
    "VALUE ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$'"
)


def preflight_pgcrypto() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            "SELECT n.nspname, p.oid FROM pg_extension e "
            "JOIN pg_namespace n ON n.oid=e.extnamespace "
            "JOIN pg_proc p ON p.pronamespace=n.oid AND p.proname='digest' "
            "WHERE e.extname='pgcrypto' AND pg_get_function_identity_arguments(p.oid)='bytea, text'"
        )
    ).one_or_none()
    if row is None:
        raise RuntimeError("PGCRYPTO_REQUIRED")
    permitted = bind.execute(
        sa.text("SELECT has_function_privilege(current_user, :function_oid, 'EXECUTE')"),
        {"function_oid": row.oid},
    ).scalar_one()
    if not permitted:
        raise RuntimeError("PGCRYPTO_DIGEST_PRIVILEGE_REQUIRED")
    try:
        schema = bind.dialect.identifier_preparer.quote(row.nspname)
        usable = bind.execute(
            sa.text(
                f"SELECT {schema}.digest('preflight'::bytea, 'sha256') = "
                "decode('6f851dc660025ec06e880e895c7210798b0ab9df426c8de40d378291775bc317', 'hex')"
            )
        ).scalar_one()
        if not usable:
            raise RuntimeError("PGCRYPTO_DIGEST_PRIVILEGE_REQUIRED")
    except Exception as exc:
        raise RuntimeError("PGCRYPTO_DIGEST_PRIVILEGE_REQUIRED") from exc
    # Ensure the pgcrypto extension schema stays resolvable so the immutable
    # release integrity CHECK (digest(manifest_bytes, 'sha256')) can be
    # created even when the caller's search_path omits the extension schema.
    current_path = bind.execute(sa.text("SHOW search_path")).scalar_one()
    entries = [entry.strip().strip('"') for entry in current_path.split(",") if entry.strip()]
    if row.nspname not in entries:
        bind.execute(
            sa.text("SELECT set_config('search_path', :path, true)"),
            {"path": ", ".join(entries + [row.nspname])},
        )


def upgrade_domain_foundation() -> None:
    op.create_table(
        "security_domains",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_security_domains"),
        sa.CheckConstraint(UUID_CHECK.replace("VALUE", "id"), name="ck_security_domains_id_uuid"),
        sa.UniqueConstraint("key", name="uq_security_domains_key"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_security_domains_status"),
    )
    op.create_index(
        "uq_security_domains_one_active",
        "security_domains",
        [sa.text("(status)")],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.execute(
        sa.text(
            "INSERT INTO security_domains (id, key, status) VALUES (:id, 'default', 'active')"
        ).bindparams(id=DEFAULT_SECURITY_DOMAIN_ID)
    )
    op.execute(
        """
        CREATE FUNCTION reject_security_domain_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'SECURITY_DOMAIN_IMMUTABLE';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER security_domains_immutable BEFORE UPDATE OR DELETE "
        "ON security_domains FOR EACH ROW EXECUTE FUNCTION reject_security_domain_mutation()"
    )

    for table_name in ("users", "ontology_projects"):
        op.add_column(
            table_name,
            sa.Column(
                "security_domain_id",
                sa.String(36),
                nullable=True,
                server_default=sa.text(f"'{DEFAULT_SECURITY_DOMAIN_ID}'"),
            ),
        )
        op.execute(
            sa.text(f"UPDATE {table_name} SET security_domain_id=:id WHERE security_domain_id IS NULL").bindparams(
                id=DEFAULT_SECURITY_DOMAIN_ID
            )
        )
        op.alter_column(table_name, "security_domain_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table_name}_security_domain",
            table_name,
            "security_domains",
            ["security_domain_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_unique_constraint(
            f"uq_{table_name}_id_security_domain",
            table_name,
            ["id", "security_domain_id"],
        )

    op.drop_constraint(
        "ontology_projects_created_by_fkey", "ontology_projects", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_ontology_projects_creator_domain",
        "ontology_projects",
        "users",
        ["created_by", "security_domain_id"],
        ["id", "security_domain_id"],
        ondelete="RESTRICT",
    )

    op.execute(
        f"""
        CREATE FUNCTION enforce_default_security_domain() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.security_domain_id IS NULL THEN
            NEW.security_domain_id := '{DEFAULT_SECURITY_DOMAIN_ID}';
          ELSIF NEW.security_domain_id <> '{DEFAULT_SECURITY_DOMAIN_ID}' THEN
            RAISE EXCEPTION 'SECURITY_DOMAIN_MISMATCH';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    for table_name in ("users", "ontology_projects"):
        op.execute(
            f"CREATE TRIGGER {table_name}_default_security_domain "
            f"BEFORE INSERT ON {table_name} FOR EACH ROW "
            "EXECUTE FUNCTION enforce_default_security_domain()"
        )

    op.create_table(
        "auth_refresh_families",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("security_domain_id", sa.String(36), nullable=False),
        sa.Column("current_generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_auth_refresh_families"),
        sa.CheckConstraint(UUID_CHECK.replace("VALUE", "id"), name="ck_auth_refresh_families_id_uuid"),
        sa.UniqueConstraint("id", "security_domain_id", name="uq_auth_refresh_family_domain"),
        sa.ForeignKeyConstraint(["security_domain_id"], ["security_domains.id"], ondelete="RESTRICT", name="fk_auth_refresh_family_domain"),
        sa.ForeignKeyConstraint(
            ["user_id", "security_domain_id"],
            ["users.id", "users.security_domain_id"],
            ondelete="RESTRICT",
            name="fk_auth_refresh_family_user_domain",
        ),
        sa.CheckConstraint("current_generation >= 0", name="ck_auth_refresh_family_generation"),
        sa.CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_auth_refresh_family_status"),
    )
    op.create_table(
        "auth_refresh_tokens",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_auth_refresh_tokens"),
        sa.CheckConstraint(UUID_CHECK.replace("VALUE", "id"), name="ck_auth_refresh_tokens_id_uuid"),
        sa.UniqueConstraint("family_id", "generation", name="uq_auth_refresh_token_generation"),
        sa.ForeignKeyConstraint(["family_id"], ["auth_refresh_families.id"], ondelete="RESTRICT", name="fk_auth_refresh_token_family"),
        sa.CheckConstraint("generation >= 0", name="ck_auth_refresh_token_generation"),
        sa.CheckConstraint("status IN ('active', 'used', 'revoked', 'expired')", name="ck_auth_refresh_token_status"),
    )
    op.execute(
        """
        CREATE FUNCTION reject_auth_refresh_token_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'AUTH_REFRESH_TOKEN_APPEND_ONLY';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER auth_refresh_tokens_append_only BEFORE UPDATE OR DELETE "
        "ON auth_refresh_tokens FOR EACH ROW EXECUTE FUNCTION reject_auth_refresh_token_mutation()"
    )


def downgrade_domain_foundation() -> None:
    op.execute("DROP TRIGGER IF EXISTS auth_refresh_tokens_append_only ON auth_refresh_tokens")
    op.execute("DROP FUNCTION IF EXISTS reject_auth_refresh_token_mutation()")
    op.drop_table("auth_refresh_tokens")
    op.drop_table("auth_refresh_families")
    for table_name in ("ontology_projects", "users"):
        op.execute(f"DROP TRIGGER IF EXISTS {table_name}_default_security_domain ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS enforce_default_security_domain()")
    for table_name in ("ontology_projects", "users"):
        if table_name == "ontology_projects":
            op.drop_constraint(
                "fk_ontology_projects_creator_domain",
                "ontology_projects",
                type_="foreignkey",
            )
            op.create_foreign_key(
                "ontology_projects_created_by_fkey",
                "ontology_projects",
                "users",
                ["created_by"],
                ["id"],
            )
        op.drop_constraint(f"uq_{table_name}_id_security_domain", table_name, type_="unique")
        op.drop_constraint(f"fk_{table_name}_security_domain", table_name, type_="foreignkey")
        op.drop_column(table_name, "security_domain_id")
    op.drop_index("uq_security_domains_one_active", table_name="security_domains")
    op.execute("DROP TRIGGER IF EXISTS security_domains_immutable ON security_domains")
    op.execute("DROP FUNCTION IF EXISTS reject_security_domain_mutation()")
    op.drop_table("security_domains")


def upgrade() -> None:
    preflight_pgcrypto()
    upgrade_domain_foundation()
    upgrade_release_foundation()
    upgrade_audit_foundation()
    upgrade_identity_foundation()
    upgrade_access_foundation()
    upgrade_cutover_guards()


def downgrade() -> None:
    downgrade_cutover_guards()
    downgrade_access_foundation()
    downgrade_identity_foundation()
    downgrade_audit_foundation()
    downgrade_release_foundation()
    downgrade_domain_foundation()
