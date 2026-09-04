"""Persist business-journey preparation evidence and snapshot binding.

Revision ID: 0036_business_journey_preparations
Revises: 0035_governed_turn_plans
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_business_journey_preparations"
down_revision = "0035_governed_turn_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_journey_preparations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(200), nullable=False),
        sa.Column("journey_id", sa.String(100), nullable=False),
        sa.Column("ontology_id", sa.String(36), sa.ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ontology_release_id", sa.String(36), sa.ForeignKey("ontology_releases.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("semantic_snapshot_id", sa.String(36), sa.ForeignKey("semantic_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("pipeline_run_id", sa.String(200), nullable=False),
        sa.Column("dataset_version_id", sa.String(200), nullable=False),
        sa.Column("curated_dataset_id", sa.String(200), nullable=False),
        sa.Column("curated_review_id", sa.String(200), nullable=False),
        sa.Column("model_config_version_id", sa.String(36), nullable=False),
        sa.Column("structured", sa.JSON(), nullable=False),
        sa.Column("model_probe", sa.JSON(), nullable=False),
        sa.Column("model_calls", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("run_id", "journey_id", name="uq_business_journey_preparation_run"),
    )


def downgrade() -> None:
    op.drop_table("business_journey_preparations")
