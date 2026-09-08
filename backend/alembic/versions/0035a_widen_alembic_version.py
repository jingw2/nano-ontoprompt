"""Widen alembic_version.version_num past Alembic's default 32 chars.

Several later revision ids (e.g. `0036_business_journey_preparations`,
`0039_business_journey_model_call_ledger`) are longer than 32 characters,
so a fresh database upgrading through this point would fail with
`StringDataRightTruncation` the moment alembic tries to stamp one of them.
Inserted here, before any revision id actually overflows, so the column is
already wide enough by the time it's needed. No feature this repo ships has
run past 0035 in any real deployment yet, so rewriting this one link in the
chain (0036's `down_revision` now points here instead of directly at 0035)
carries no upgrade-path risk.

Revision ID: 0035a_widen_alembic_version
Revises: 0035_governed_turn_plans
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0035a_widen_alembic_version"
down_revision = "0035_governed_turn_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "alembic_version", "version_num",
        existing_type=sa.String(32), type_=sa.String(255), existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "alembic_version", "version_num",
        existing_type=sa.String(255), type_=sa.String(32), existing_nullable=False,
    )
