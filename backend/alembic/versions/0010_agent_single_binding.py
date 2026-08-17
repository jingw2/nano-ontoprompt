"""Agent binds exactly one published Ontology (P2B-TOOLS single-binding).

Adds a UNIQUE constraint on `agent_ontology_bindings.agent_version_id`: an
Agent (version) may have at most one ontology-binding child row.  Enforced
at the API layer too (`_validate_ontology_bindings` rejects >1 binding in
the request payload); this constraint is defense-in-depth against direct
service-layer writes.
"""

from alembic import op

revision = "0010_agent_single_binding"
down_revision = "0009_agent_category_selection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_agent_ontology_binding_single",
        "agent_ontology_bindings",
        ["agent_version_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_agent_ontology_binding_single",
        "agent_ontology_bindings",
        type_="unique",
    )
