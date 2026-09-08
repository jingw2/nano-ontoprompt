"""OAuth 2.0 + PKCE authorization server (P7E plan 1 of 2).

Public-client-only (PKCE, no client secret) Authorization Code flow.
oauth_clients are admin-registered (no dynamic client registration).
oauth_authorization_codes are single-use and hashed at rest.
oauth_refresh_families/oauth_refresh_tokens mirror auth_refresh_families/
auth_refresh_tokens' rotating-generation, reuse-detecting design exactly,
parameterized by oauth client instead of browser session. Access tokens are
stateless signed JWTs (token_use=oauth_access claim), not stored here.

Revision ID: 0016_oauth_pkce
Revises: 0015_external_mcp
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_oauth_pkce"
down_revision = "0015_external_mcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_name", sa.String(200), nullable=False),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("allowed_scopes", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="ck_oauth_clients_id_uuid",
        ),
    )
    op.create_table(
        "oauth_authorization_codes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.String, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("code_challenge_method", sa.String(10), nullable=False),
        sa.Column("scope", sa.String(500), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="ck_oauth_auth_codes_id_uuid",
        ),
        sa.CheckConstraint("code_challenge_method IN ('S256')", name="ck_oauth_auth_codes_method"),
    )
    op.create_table(
        "oauth_refresh_families",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.String, nullable=False),
        sa.Column("security_domain_id", sa.String(36), sa.ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scope", sa.String(500), nullable=False, server_default=""),
        sa.Column("current_generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="ck_oauth_refresh_families_id_uuid",
        ),
        sa.CheckConstraint("current_generation >= 0", name="ck_oauth_refresh_family_generation"),
        sa.CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_oauth_refresh_family_status"),
        sa.UniqueConstraint("id", "security_domain_id", name="uq_oauth_refresh_family_domain"),
        sa.ForeignKeyConstraint(
            ["user_id", "security_domain_id"], ["users.id", "users.security_domain_id"],
            ondelete="RESTRICT", name="fk_oauth_refresh_family_user_domain",
        ),
    )
    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(36), sa.ForeignKey("oauth_refresh_families.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="ck_oauth_refresh_tokens_id_uuid",
        ),
        sa.CheckConstraint("generation >= 0", name="ck_oauth_refresh_token_generation"),
        sa.CheckConstraint("status IN ('active', 'used', 'revoked', 'expired')", name="ck_oauth_refresh_token_status"),
        sa.UniqueConstraint("family_id", "generation", name="uq_oauth_refresh_token_generation"),
    )


def downgrade() -> None:
    op.drop_table("oauth_refresh_tokens")
    op.drop_table("oauth_refresh_families")
    op.drop_table("oauth_authorization_codes")
    op.drop_table("oauth_clients")
