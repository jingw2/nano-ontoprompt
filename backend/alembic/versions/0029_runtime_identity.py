"""Trusted delegated runtime credentials (Task 13).

Extends the registered OAuth client contract so a client row can also serve
as a registered Agent/service identity for delegated-credential issuance:
`security_domain_id` (for the same-security-domain check against the
delegated user), `allowed_audiences`, and `capability_names`. Adds
`runtime_delegated_credentials`, a hashed-at-rest record (mirrors
`oauth_authorization_codes`/`oauth_refresh_tokens`) of every short-lived
token-exchange credential issued, carrying only the metadata needed to deny
expired, revoked, wrong-audience, or wrong-scope presentations without ever
storing the bearer token itself.

Revision ID: 0029_runtime_identity
Revises: 0028_snapshot_release_published
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_runtime_identity"
down_revision = "0028_snapshot_release_published"
branch_labels = None
depends_on = None

DEFAULT_SECURITY_DOMAIN_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.add_column(
        "oauth_clients",
        sa.Column(
            "security_domain_id", sa.String(36),
            sa.ForeignKey("security_domains.id", ondelete="RESTRICT"),
            nullable=False, server_default=DEFAULT_SECURITY_DOMAIN_ID,
        ),
    )
    op.add_column(
        "oauth_clients",
        sa.Column("allowed_audiences", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.add_column(
        "oauth_clients",
        sa.Column("capability_names", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )

    op.create_table(
        "runtime_delegated_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("user_id", sa.String, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("audience", sa.String(200), nullable=False),
        sa.Column("scope", sa.String(500), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="ck_runtime_delegated_credentials_id_uuid",
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_runtime_delegated_credentials_status"),
    )


def downgrade() -> None:
    op.drop_table("runtime_delegated_credentials")
    op.drop_column("oauth_clients", "capability_names")
    op.drop_column("oauth_clients", "allowed_audiences")
    op.drop_column("oauth_clients", "security_domain_id")
