"""Persist the granted MCP descriptor ids on business_journey_preparations.

Revision ID: 0038_preparation_mcp_descriptors
Revises: 0037_curated_review_pipeline_run
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_preparation_mcp_descriptors"
down_revision = "0037_curated_review_pipeline_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "business_journey_preparations",
        sa.Column("mcp_descriptor_ids", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("business_journey_preparations", "mcp_descriptor_ids")
