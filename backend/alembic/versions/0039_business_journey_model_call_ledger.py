"""Persist cross-phase business-journey model-call slots."""
from alembic import op
import sqlalchemy as sa

revision = "0039_business_journey_model_call_ledger"
down_revision = "0038_preparation_mcp_descriptors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_journey_model_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(200), nullable=False),
        sa.Column("journey_id", sa.String(100), nullable=False),
        sa.Column("logical_call_index", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("call_kind", sa.String(40), nullable=False),
        sa.Column("correlation_id", sa.String(400), nullable=False),
        sa.Column("model_config_version_id", sa.String(36), nullable=False),
        sa.Column("requested_model", sa.String(200)), sa.Column("observed_model", sa.String(200)),
        sa.Column("http_attempts", sa.Integer()), sa.Column("retry_count", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "journey_id", "logical_call_index", name="uq_business_journey_model_call_slot"),
    )


def downgrade() -> None:
    op.drop_table("business_journey_model_calls")
