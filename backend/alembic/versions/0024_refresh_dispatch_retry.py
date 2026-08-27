"""Task 7 review fix: durable dispatch claims and bounded publish retry.

Adds an owner/expiry pair so concurrent beat workers cannot publish the same
retained occurrence, plus the next retry instant used with RefreshRun.retry_count
to honor each schedule's bounded retry policy.

Revision ID: 0024_refresh_dispatch_retry
Revises: 0023_refresh_schedule_admission
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_refresh_dispatch_retry"
down_revision = "0023_refresh_schedule_admission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("refresh_runs", sa.Column("dispatch_claim_owner", sa.String(200), nullable=True))
    op.add_column(
        "refresh_runs",
        sa.Column("dispatch_claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("refresh_runs", sa.Column("dispatch_retry_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("refresh_runs", "dispatch_retry_at")
    op.drop_column("refresh_runs", "dispatch_claim_expires_at")
    op.drop_column("refresh_runs", "dispatch_claim_owner")
