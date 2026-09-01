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
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
UUID_GLOB = (
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]"
)


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _uuid_check(column: str) -> str:
    dialect = _dialect_name()
    if dialect == "postgresql":
        return f"{column} ~ '{UUID_PATTERN}'"
    if dialect == "mysql":
        return f"{column} REGEXP '{UUID_PATTERN}'"
    if dialect == "sqlite":
        return f"{column} GLOB '{UUID_GLOB}'"
    return f"length({column}) = 36"


def _json_server_default(kind: str) -> sa.TextClause:
    """Return a JSON default accepted by each supported SQL dialect."""
    if _dialect_name() == "mysql":
        function = "JSON_ARRAY()" if kind == "array" else "JSON_OBJECT()"
        return sa.text(f"({function})")
    value = "[]" if kind == "array" else "{}"
    return sa.text(f"'{value}'")


def _oauth_client_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column(
            "security_domain_id", sa.String(36),
            sa.ForeignKey(
                "security_domains.id", ondelete="RESTRICT",
                name="fk_oauth_clients_security_domain",
            ), nullable=True,
        ),
        sa.Column("allowed_audiences", sa.JSON(), nullable=True),
        sa.Column("capability_names", sa.JSON(), nullable=True),
    )


def _add_oauth_client_columns() -> None:
    columns = _oauth_client_columns()
    if _dialect_name() == "sqlite":
        with op.batch_alter_table("oauth_clients", recreate="always") as batch_op:
            for column in columns:
                batch_op.add_column(column)
    else:
        for column in columns:
            op.add_column("oauth_clients", column)


def _backfill_oauth_client_columns() -> None:
    oauth_clients = sa.table(
        "oauth_clients",
        sa.column("security_domain_id", sa.String(36)),
        sa.column("allowed_audiences", sa.JSON()),
        sa.column("capability_names", sa.JSON()),
    )
    op.execute(
        oauth_clients.update()
        .where(oauth_clients.c.security_domain_id.is_(None))
        .values(security_domain_id=DEFAULT_SECURITY_DOMAIN_ID)
    )
    for column in (oauth_clients.c.allowed_audiences, oauth_clients.c.capability_names):
        op.execute(
            oauth_clients.update()
            .where(column.is_(None))
            .values({column.key: _json_server_default("array")})
        )


def _make_oauth_client_columns_required() -> None:
    defaults = {
        "security_domain_id": sa.text(f"'{DEFAULT_SECURITY_DOMAIN_ID}'"),
        "allowed_audiences": _json_server_default("array"),
        "capability_names": _json_server_default("array"),
    }
    if _dialect_name() == "sqlite":
        with op.batch_alter_table("oauth_clients", recreate="always") as batch_op:
            batch_op.alter_column(
                "security_domain_id", existing_type=sa.String(36),
                nullable=False, server_default=defaults["security_domain_id"],
            )
            for name in ("allowed_audiences", "capability_names"):
                batch_op.alter_column(
                    name, existing_type=sa.JSON(), nullable=False,
                    server_default=defaults[name],
                )
    else:
        op.alter_column(
            "oauth_clients", "security_domain_id", existing_type=sa.String(36),
            nullable=False, server_default=defaults["security_domain_id"],
        )
        for name in ("allowed_audiences", "capability_names"):
            op.alter_column(
                "oauth_clients", name, existing_type=sa.JSON(),
                nullable=False, server_default=defaults[name],
            )


def _drop_oauth_client_columns() -> None:
    if _dialect_name() == "sqlite":
        with op.batch_alter_table("oauth_clients", recreate="always") as batch_op:
            for name in ("capability_names", "allowed_audiences", "security_domain_id"):
                batch_op.drop_column(name)
    else:
        for name in ("capability_names", "allowed_audiences", "security_domain_id"):
            op.drop_column("oauth_clients", name)


def upgrade() -> None:
    _add_oauth_client_columns()
    _backfill_oauth_client_columns()
    _make_oauth_client_columns_required()

    op.create_table(
        "runtime_delegated_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("audience", sa.String(200), nullable=False),
        sa.Column("scope", sa.String(500), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            _uuid_check("id"),
            name="ck_runtime_delegated_credentials_id_uuid",
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_runtime_delegated_credentials_status"),
    )


def downgrade() -> None:
    op.drop_table("runtime_delegated_credentials")
    _drop_oauth_client_columns()
