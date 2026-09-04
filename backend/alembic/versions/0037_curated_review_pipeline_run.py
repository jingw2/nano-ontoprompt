"""Bind curated reviews to the pipeline run that produced their dataset."""
from alembic import op
import sqlalchemy as sa

revision = "0037_curated_review_pipeline_run"
down_revision = "0036_business_journey_preparations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("v2_curated_reviews", sa.Column("pipeline_run_id", sa.String(200), nullable=True))
    op.create_index("ix_v2_curated_reviews_pipeline_run_id", "v2_curated_reviews", ["pipeline_run_id"])


def downgrade() -> None:
    op.drop_index("ix_v2_curated_reviews_pipeline_run_id", table_name="v2_curated_reviews")
    op.drop_column("v2_curated_reviews", "pipeline_run_id")
