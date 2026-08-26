"""Store the human-readable entity class name alongside v2_ontology_mappings.

AutoMapper already computes entity_class_cn (LLM or rule-based), but it was
discarded after /mappings/suggest — nothing persisted it, so mapping_service
regenerated a generic camelCase-splitter name from entity_class instead of
using the human-authored one. This column lets create_mapping store it.

Revision ID: 0021_mapping_entity_class_cn
Revises: 0020_agent_memory_recall_index
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_mapping_entity_class_cn"
down_revision = "0020_agent_memory_recall_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "v2_ontology_mappings",
        sa.Column("entity_class_cn", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v2_ontology_mappings", "entity_class_cn")
