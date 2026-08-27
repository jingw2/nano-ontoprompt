"""Persist refresh provenance on immutable dataset versions.

Revision ID: 0025_dataset_version_provenance
Revises: 0024_refresh_dispatch_retry
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa


revision = "0025_dataset_version_provenance"
down_revision = "0024_refresh_dispatch_retry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("v2_dataset_versions", sa.Column("refresh_run_id", sa.String(200), nullable=True))
    op.add_column("v2_dataset_versions", sa.Column("source_cursor", sa.JSON(), nullable=True))
    op.add_column(
        "v2_dataset_versions",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v2_dataset_versions", "observed_at")
    op.drop_column("v2_dataset_versions", "source_cursor")
    op.drop_column("v2_dataset_versions", "refresh_run_id")
