"""Agent runtime migration (P3A-INSTANCE: instance revision/relation sections).

Revision ID: 0006_agent_runtime
Revises: 0005_agent_configuration

P3A-INSTANCE owns the authoritative-instance sections: EntityInstance gains
`revision`, timezone `updated_at`, nullable `deleted_at` and a unique
`(ontology_id, entity_id, row_identity)` identity (created only when the
duplicate preflight is clean — duplicates are reported, never silently
deduped); `entity_id` FK changes CASCADE -> RESTRICT; Entity/Relation gain
soft-deprecation columns; `entity_instance_relations` is created with RESTRICT
FKs and a partial unique active-edge index.  Runtime/agent tables are added by
P3A-TURNDB in the same revision after this packet's handoff.
"""
import uuid

from alembic import op
import sqlalchemy as sa

from app.services.publication.cutover import (
    downgrade_instance_edge_guards,
    upgrade_instance_edge_guards,
)


revision = "0006_agent_runtime"
down_revision = "0005_agent_configuration"
branch_labels = None
depends_on = None

UUID = sa.String(36)
ENTITY_INSTANCE_FK = "entity_instances_entity_id_fkey"


def preflight_instance_duplicates(connection) -> list[dict]:
    """Duplicate (ontology_id, entity_id, row_identity) report.  Never aborts
    DDL; the unique identity index is created only when the report is empty."""
    rows = connection.execute(sa.text(
        "SELECT ontology_id, entity_id, row_identity, count(*) AS cnt "
        "FROM entity_instances "
        "GROUP BY ontology_id, entity_id, row_identity HAVING count(*) > 1 "
        "ORDER BY ontology_id, entity_id, row_identity"
    )).mappings().all()
    return [dict(row) for row in rows]


def upgrade_instance_revision_foundation() -> None:
    # EntityInstance: revision / updated_at / deleted_at + RESTRICT entity FK
    # (Entity/Relation soft-deprecation columns already exist since 0003).
    op.add_column("entity_instances", sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"))
    op.add_column("entity_instances", sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    ))
    op.add_column("entity_instances", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint(ENTITY_INSTANCE_FK, "entity_instances", type_="foreignkey")
    op.create_foreign_key(ENTITY_INSTANCE_FK, "entity_instances", "entities",
                          ["entity_id"], ["id"], ondelete="RESTRICT")

    # Unique identity index — only when the duplicate preflight is clean
    bind = op.get_bind()
    duplicates = preflight_instance_duplicates(bind)
    if duplicates:
        op.create_index("ix_entity_instances_identity", "entity_instances",
                        ["ontology_id", "entity_id", "row_identity"])
    else:
        op.create_index("uq_entity_instances_identity", "entity_instances",
                        ["ontology_id", "entity_id", "row_identity"], unique=True)

    # Authoritative instance relations (RESTRICT everywhere, active-edge unique)
    op.create_table(
        "entity_instance_relations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("ontology_id", UUID, nullable=False),
        sa.Column("source_instance_id", UUID, nullable=False),
        sa.Column("target_instance_id", UUID, nullable=False),
        sa.Column("relation_definition_id", UUID, nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_entity_instance_relations"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], name="fk_eir_ontology", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_instance_id"], ["entity_instances.id"], name="fk_eir_source", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_instance_id"], ["entity_instances.id"], name="fk_eir_target", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["relation_definition_id"], ["relations.id"], name="fk_eir_relation", ondelete="RESTRICT"),
    )
    op.create_index("ix_entity_instance_relations_ontology", "entity_instance_relations", ["ontology_id"])
    op.create_index("ix_entity_instance_relations_source", "entity_instance_relations", ["source_instance_id"])
    op.create_index("ix_entity_instance_relations_target", "entity_instance_relations", ["target_instance_id"])
    op.create_index("ix_entity_instance_relations_relation", "entity_instance_relations", ["relation_definition_id"])
    op.create_index(
        "uq_entity_instance_relations_active_edge", "entity_instance_relations",
        ["source_instance_id", "target_instance_id", "relation_definition_id"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade_instance_revision_foundation() -> None:
    op.drop_index("uq_entity_instance_relations_active_edge", table_name="entity_instance_relations")
    op.drop_table("entity_instance_relations")
    op.drop_index("uq_entity_instances_identity", table_name="entity_instances")
    op.drop_index("ix_entity_instances_identity", table_name="entity_instances")
    op.drop_constraint(ENTITY_INSTANCE_FK, "entity_instances", type_="foreignkey")
    op.create_foreign_key(ENTITY_INSTANCE_FK, "entity_instances", "entities",
                          ["entity_id"], ["id"], ondelete="CASCADE")
    op.drop_column("entity_instances", "deleted_at")
    op.drop_column("entity_instances", "updated_at")
    op.drop_column("entity_instances", "revision")


def upgrade() -> None:
    upgrade_instance_revision_foundation()
    upgrade_instance_edge_guards()


def downgrade() -> None:
    downgrade_instance_edge_guards()
    downgrade_instance_revision_foundation()
