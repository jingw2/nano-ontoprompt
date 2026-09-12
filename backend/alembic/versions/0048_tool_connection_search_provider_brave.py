"""Add 'brave' to tool_connection_versions.search_provider.

Revision ID: 0048_tool_connection_search_provider_brave
Revises: 0047_tool_connection_search_provider
Create Date: 2026-09-12

Brave Search API is a GET request with an `X-Subscription-Token` header —
a fourth real shape alongside the Bing/Google/Serper ones 0047 added.
"""
from alembic import op

revision = "0048_tool_connection_search_provider_brave"
down_revision = "0047_tool_connection_search_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_tcv_search_provider", "tool_connection_versions", type_="check")
    op.create_check_constraint(
        "ck_tcv_search_provider",
        "tool_connection_versions",
        "search_provider IS NULL OR search_provider IN ('generic', 'bing', 'google', 'serper', 'brave')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tcv_search_provider", "tool_connection_versions", type_="check")
    op.create_check_constraint(
        "ck_tcv_search_provider",
        "tool_connection_versions",
        "search_provider IS NULL OR search_provider IN ('generic', 'bing', 'google', 'serper')",
    )
