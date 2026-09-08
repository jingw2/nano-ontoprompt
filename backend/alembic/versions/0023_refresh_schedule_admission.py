"""Task 7: persisted T+1/cron schedule controls — adds the
`max_pending_runs` column that `dispatch_due_schedules` checks before
publishing a due schedule's run, plus durable destination-queue attribution
for refresh operability metrics. A saturated source stays durably queued with
a backpressure reason instead of unboundedly piling up runs.

0022_refresh_contract created `refresh_schedules` without this column since
nothing dispatched through it yet; this is the first migration to build on
top of that table.

Revision ID: 0023_refresh_schedule_admission
Revises: 0022_refresh_contract
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_refresh_schedule_admission"
down_revision = "0022_refresh_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "refresh_schedules",
        sa.Column("max_pending_runs", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "refresh_runs",
        sa.Column("dispatch_queue", sa.String(120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("refresh_runs", "dispatch_queue")
    op.drop_column("refresh_schedules", "max_pending_runs")
