"""Agent ontology-binding tool catalog limit (context-budget scoping).

Adds `tool_catalog_limit` to `agent_ontology_bindings`: an optional cap on
how many category-derived tool descriptors (Logic rules + Actions not
explicitly in `selected_tools`) get materialized into LLM tool schemas each
turn for that binding. NULL (default) preserves today's unlimited behavior;
a present value bounds a large auto-discovered ontology's tool catalog so it
does not exceed the model's context budget before the user's message is
even read.

Revision ID: 0043_ontology_tool_catalog_limit
Revises: 0042_logic_action_palantir_model
Create Date: 2026-09-09
"""

from alembic import op
import sqlalchemy as sa

revision = "0043_ontology_tool_catalog_limit"
down_revision = "0042_logic_action_palantir_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_ontology_bindings",
        sa.Column("tool_catalog_limit", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_ontology_bindings", "tool_catalog_limit")
