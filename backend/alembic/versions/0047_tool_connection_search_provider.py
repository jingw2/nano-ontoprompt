"""Add search_provider to tool_connection_versions.

Revision ID: 0047_tool_connection_search_provider
Revises: 0046_backfill_legacy_logic_action_mirrors
Create Date: 2026-09-12

A `search`-kind connection version previously only stored a raw `endpoint` +
`credential_reference` and always called it the same way (a GET request with
`Authorization: Bearer <key>` and `?q=&count=` query params) — a shape none
of the real search APIs an admin plausibly wires up here actually use: Bing
Web Search v7 expects an `Ocp-Apim-Subscription-Key` header, Google Custom
Search puts the key in a `key=` query param (plus a required `cx` engine id
folded into the endpoint URL), and Serper.dev is a POST with an `X-API-KEY`
header and a JSON body. `search_provider` records which of these known
shapes (or `generic` for the original behavior) `app/services/tools/search.py`
should use to build the request; NULL keeps the original generic behavior
for any version created before this column existed.
"""
from alembic import op
import sqlalchemy as sa

revision = "0047_tool_connection_search_provider"
down_revision = "0046_backfill_legacy_logic_action_mirrors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tool_connection_versions",
        sa.Column("search_provider", sa.String(20), nullable=True),
    )
    op.create_check_constraint(
        "ck_tcv_search_provider",
        "tool_connection_versions",
        "search_provider IS NULL OR search_provider IN ('generic', 'bing', 'google', 'serper')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tcv_search_provider", "tool_connection_versions", type_="check")
    op.drop_column("tool_connection_versions", "search_provider")
