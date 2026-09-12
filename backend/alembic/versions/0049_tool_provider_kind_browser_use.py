"""Add 'browser_use' to tool_providers.kind.

Revision ID: 0049_tool_provider_kind_browser_use
Revises: 0048_tool_connection_search_provider_brave
Create Date: 2026-09-14

Browser Use is a fourth "live" external tool kind alongside search/
playwright/external_mcp: a fixed endpoint+credential connection that
delegates a natural-language browsing task to an external Browser
Use-compatible HTTP service (self-hosted or third-party), distinct from
Playwright's single in-process page fetch.
"""
from alembic import op

revision = "0049_tool_provider_kind_browser_use"
down_revision = "0048_tool_connection_search_provider_brave"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_tool_providers_kind", "tool_providers", type_="check")
    op.create_check_constraint(
        "ck_tool_providers_kind",
        "tool_providers",
        "kind IN ('search', 'playwright', 'skill', 'external_mcp', 'ontology_mcp', 'browser_use')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tool_providers_kind", "tool_providers", type_="check")
    op.create_check_constraint(
        "ck_tool_providers_kind",
        "tool_providers",
        "kind IN ('search', 'playwright', 'skill', 'external_mcp', 'ontology_mcp')",
    )
