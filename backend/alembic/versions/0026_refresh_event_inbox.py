"""Persist replayable managed refresh event envelopes and replay audits.

Revision ID: 0026_refresh_event_inbox
Revises: 0025_dataset_version_provenance
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision = "0026_refresh_event_inbox"
down_revision = "0025_dataset_version_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "refresh_inbox_events",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("refresh_runs.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("refresh_inbox_events", sa.Column("envelope_json", sa.JSON(), nullable=True))
    op.create_index("ix_refresh_inbox_events_run_id", "refresh_inbox_events", ["run_id"])

    op.add_column("refresh_dead_letters", sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("refresh_dead_letters", sa.Column("replayed_by", sa.String(200), nullable=True))
    op.add_column(
        "refresh_dead_letters",
        sa.Column("replay_run_id", sa.String(36), sa.ForeignKey("refresh_runs.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_refresh_dead_letters_replay_run_id", "refresh_dead_letters", ["replay_run_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_dead_letters_replay_run_id", table_name="refresh_dead_letters")
    op.drop_column("refresh_dead_letters", "replay_run_id")
    op.drop_column("refresh_dead_letters", "replayed_by")
    op.drop_column("refresh_dead_letters", "replayed_at")
    op.drop_index("ix_refresh_inbox_events_run_id", table_name="refresh_inbox_events")
    op.drop_column("refresh_inbox_events", "envelope_json")
    op.drop_column("refresh_inbox_events", "run_id")
