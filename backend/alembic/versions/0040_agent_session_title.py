"""Add a title column to agent_sessions, derived from the session's first message.

Revision ID: 0040_agent_session_title
Revises: 0039_business_journey_model_call_ledger
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_agent_session_title"
down_revision = "0039_business_journey_model_call_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("title", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_sessions", "title")
