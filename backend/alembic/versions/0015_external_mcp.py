"""External MCP client schema-pin and OAuth-token tables (P7D external tools).

mcp_connection_schemas pins the remote MCP server's `tools/list` response
(name/description/input schema per tool) at admin-approval time; dispatch
re-fetches and re-hashes it on every call and quarantines on drift — the
same discipline as signed Skills' canonical-hash recheck (see
app/services/skills/__init__.py). mcp_oauth_tokens stores encrypted,
scoped, rotatable confidential-client bearer tokens. Both hang off
tool_connection_versions (kind='external_mcp', pre-declared by
0012_tool_provider_kind) — no new provider/connection/binding tables.

Revision ID: 0015_external_mcp
Revises: 0014_signed_skills
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_external_mcp"
down_revision = "0014_signed_skills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_connection_schemas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connection_version_id", sa.String(36),
                   sa.ForeignKey("tool_connection_versions.id", ondelete="RESTRICT"),
                   nullable=False, unique=True, index=True),
        sa.Column("tool_schema_hash", sa.String(64), nullable=False),
        sa.Column("tools", sa.JSON(), nullable=False),
        sa.Column("quarantined", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantined_reason", sa.String(200), nullable=True),
        sa.Column("pinned_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "mcp_oauth_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connection_version_id", sa.String(36),
                   sa.ForeignKey("tool_connection_versions.id", ondelete="RESTRICT"),
                   nullable=False, unique=True, index=True),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_type", sa.String(20), nullable=False, server_default="Bearer"),
        sa.Column("scope", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("audience", sa.String(200), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("mcp_oauth_tokens")
    op.drop_table("mcp_connection_schemas")
