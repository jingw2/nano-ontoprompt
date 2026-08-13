"""Legacy runtime tables producer: `entity_instances` and `audit_tasks`.

`0001_full_baseline` never created these tables although the ORM registers
them, so fresh PostgreSQL returned "relation does not exist" for
instance reads, `POST /audit`, and extraction finalization.  This helper
creates exactly the current ORM shapes of `entity_instances`
(`app/models/entity_instance.py`) and `audit_tasks`
(`app/models/audit_task.py`) column-for-column, including indexes and FKs as
the ORM declares them, and is consumed by revision 0003
(P1A-INTEGRATE owns the wiring; LEGACY-SCHEMA mini-packet).
"""
from alembic import op
import sqlalchemy as sa


def upgrade_legacy_runtime_tables() -> None:
    op.create_table(
        "entity_instances",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("ontology_id", sa.String(), nullable=False),
        sa.Column("row_identity", sa.String(length=200), nullable=False),
        sa.Column("row_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_entity_instances"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_entity_instances_row_identity", "entity_instances", ["row_identity"])
    op.create_table(
        "audit_tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ontology_id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=True),
        sa.Column("react_trace", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_tasks"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["model_configs.id"], ondelete="SET NULL"),
    )


def downgrade_legacy_runtime_tables() -> None:
    op.drop_table("audit_tasks")
    op.drop_index("ix_entity_instances_row_identity", table_name="entity_instances")
    op.drop_table("entity_instances")
