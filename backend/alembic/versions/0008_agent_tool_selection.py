"""Agent ontology-binding tool selection (P2B-TOOLS).

Adds `selected_tools` to `agent_ontology_bindings`: the exact published tool
descriptor ids (built-in query + Logic + Actions) the Agent enables per bound
ontology.  The immutable version tree keeps the selection per version; old
versions stay byte-identical.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_agent_tool_selection"
down_revision = "0007_widen_audit_correlation_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_ontology_bindings",
        sa.Column(
            "selected_tools",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_ontology_bindings", "selected_tools")
