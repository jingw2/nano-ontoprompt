"""Add tool_providers.kind (P7A external tools).

Distinguishes which adapter a provider's connections resolve to (search,
playwright, skill, external_mcp, ontology_mcp — the five Phase-7 tool
families named in docs/superpowers/plans/2026-08-09-agent-ontology-
implementation.md section 13.1). Only 'search' has an adapter as of this
migration; the other four are pre-declared so later Phase-7 sub-plans need
no further schema change on this table.

Revision ID: 0012_tool_provider_kind
Revises: 0011_retention_governance
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_tool_provider_kind"
down_revision = "0011_retention_governance"
branch_labels = None
depends_on = None

PROVIDER_KINDS = ("search", "playwright", "skill", "external_mcp", "ontology_mcp")


def upgrade() -> None:
    op.add_column(
        "tool_providers",
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="search"),
    )
    op.alter_column("tool_providers", "kind", server_default=None)
    op.create_check_constraint(
        "ck_tool_providers_kind",
        "tool_providers",
        "kind IN ('search', 'playwright', 'skill', 'external_mcp', 'ontology_mcp')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tool_providers_kind", "tool_providers", type_="check")
    op.drop_column("tool_providers", "kind")
