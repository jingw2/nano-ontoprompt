"""Widen governance audit correlation_id (wc: prefix overflows varchar(64)).

Revision ID: 0007_widen_audit_correlation_id
Revises: 0006_agent_runtime
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_widen_audit_correlation_id"
down_revision = "0006_agent_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("governance_audit_outbox", "correlation_id",
                    type_=sa.String(128), existing_type=sa.String(64), existing_nullable=False)
    op.alter_column("governance_audit_logs", "correlation_id",
                    type_=sa.String(128), existing_type=sa.String(64), existing_nullable=True)


def downgrade() -> None:
    op.alter_column("governance_audit_logs", "correlation_id",
                    type_=sa.String(64), existing_type=sa.String(128), existing_nullable=True)
    op.alter_column("governance_audit_outbox", "correlation_id",
                    type_=sa.String(64), existing_type=sa.String(128), existing_nullable=False)
