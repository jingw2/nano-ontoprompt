"""Add name to tool_connections.

Revision ID: 0051_tool_connection_name
Revises: 0050_skill_version_scan_report
Create Date: 2026-09-15

A connection has no name of its own — the admin UI derives a label from its
active version's search_provider/endpoint (see list_connections()), but
that falls back to a generic provider-kind label whenever multiple
connections of the same kind exist with no active version yet (e.g. three
never-activated search connections all show "网页搜索"). `name` lets an
admin tell them apart; NULL keeps the derived-label fallback.
"""
from alembic import op
import sqlalchemy as sa

revision = "0051_tool_connection_name"
down_revision = "0050_skill_version_scan_report"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tool_connections",
        sa.Column("name", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tool_connections", "name")
