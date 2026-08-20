"""MCP-native write-approval requests (P7E plan 2 of 2).

Deliberately decoupled from agent_tool_executions/agent_approvals, which are
hard NOT-NULL-FK'd to agent_turns — an MCP tool call is not a Turn. Reuses
preview_action (backend/app/services/actions/preview.py, already
Turn-agnostic) for the preview/hash computation; this table only persists
the result and a pending/approved/rejected status. No effect-application
step exists anywhere in this design, matching execute_approved_action's
existing documented no-op for the Agent-Turn path.

Revision ID: 0017_mcp_write_requests
Revises: 0016_oauth_pkce
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_mcp_write_requests"
down_revision = "0016_oauth_pkce"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_write_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("oauth_client_id", sa.String(36), sa.ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.String, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("ontology_id", sa.String, sa.ForeignKey("ontology_projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("release_id", sa.String(36), sa.ForeignKey("ontology_releases.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("descriptor_id", sa.String(200), nullable=False),
        sa.Column("target_instance_id", sa.String, nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("preview_canonical", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_mcp_write_requests_status"),
    )


def downgrade() -> None:
    op.drop_table("mcp_write_requests")
