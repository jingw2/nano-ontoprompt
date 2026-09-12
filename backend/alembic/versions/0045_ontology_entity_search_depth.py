"""Agent ontology-binding entity search depth (multi-hop relation traversal).

Adds `entity_search_depth` to `agent_ontology_bindings`: an optional cap on
how many relation hops `ontology.traverse_relations` may walk out from the
starting instance for that binding. NULL (default) falls back to the
runtime default of 10 hops; a present value lets an operator raise it for
ontologies with deep relation chains (more traversal = more tool-call time
and more context spent per call) or lower it to keep traversal calls cheap.

Revision ID: 0045_ontology_entity_search_depth
Revises: 0044_index_outbox_skip_deleted_ontology
Create Date: 2026-09-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0045_ontology_entity_search_depth"
down_revision = "0044_index_outbox_skip_deleted_ontology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_ontology_bindings",
        sa.Column("entity_search_depth", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_ontology_bindings", "entity_search_depth")
