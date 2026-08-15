"""Agent ontology-binding category selection (P2B-TOOLS categories).

Adds `enabled_categories` to `agent_ontology_bindings`: the tool CATEGORIES
(MCP / query / write / logic / action) the Agent enables per bound ontology,
defaulting to ALL.  The column is nullable — NULL means the binding predates
category selection and stays on the legacy `selected_tools`-only filter; a
present value (possibly an empty list) switches the runtime to category-mode
filtering.  The immutable version tree keeps the selection per version; old
versions stay byte-identical.
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_agent_category_selection"
down_revision = "0008_agent_tool_selection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_ontology_bindings",
        sa.Column("enabled_categories", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_ontology_bindings", "enabled_categories")
