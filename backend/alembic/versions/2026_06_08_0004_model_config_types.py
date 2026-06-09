"""add model config type and options

Revision ID: 2026_06_08_0004
Revises: 2026_06_06_0003
Create Date: 2026-06-08
"""
from alembic import op
import sqlalchemy as sa


revision = "2026_06_08_0004"
down_revision = "2026_06_06_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("model_configs", sa.Column("config_type", sa.String(30), nullable=False, server_default="llm"))
    op.add_column("model_configs", sa.Column("options", sa.JSON(), nullable=True, server_default=sa.text("'{}'")))


def downgrade():
    op.drop_column("model_configs", "options")
    op.drop_column("model_configs", "config_type")
