"""Add max_tool_rounds to agent_versions.

Revision ID: 0052_agent_max_tool_rounds
Revises: 0051_tool_connection_name
Create Date: 2026-09-16

The Agent's tool-calling loop (`LangGraphRuntime._run_model_loop`) always
stopped after a hardcoded 5 rounds (`TOOL_ROUND_LIMIT`), with no way for an
Agent owner to raise or lower it for their own use case. This makes the
cap a per-version, versioned setting like `memory_settings` — existing
Agents default to 5 (byte-identical behavior) until an owner explicitly
changes it, which creates a new version.
"""
from alembic import op
import sqlalchemy as sa

revision = "0052_agent_max_tool_rounds"
down_revision = "0051_tool_connection_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_versions",
        sa.Column("max_tool_rounds", sa.Integer(), nullable=False, server_default="5"),
    )


def downgrade() -> None:
    op.drop_column("agent_versions", "max_tool_rounds")
