"""P6B-1: short-term Agent memory — rolling summary table.

Session-scoped, not user/Agent-namespaced (unlike P6B-2's long-term
`agent_memories`, which is namespaced by security domain/Agent/user and
outlives any one session). One row per session, upserted in place at each
regeneration — the spec's "regenerates only at threshold" and "unsupported
fields... retain the prior summary" mean a session has exactly one current
summary, not an append-only revision log (unlike long-term memories, which
DO need a revision log for user corrections — that's P6B-2's concern).

Revision ID: 0018_agent_memory_short_term
Revises: 0017_mcp_write_requests
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_agent_memory_short_term"
down_revision = "0017_mcp_write_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_memory_summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36),
                  sa.ForeignKey("agent_sessions.id", ondelete="RESTRICT"),
                  nullable=False, unique=True),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("covers_from_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("covers_to_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("source_message_hash", sa.String(64), nullable=False),
        sa.Column("summary_model_name", sa.String(200), nullable=False),
        sa.Column("summary_token_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_agent_memory_summaries_session_id", "agent_memory_summaries", ["session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_memory_summaries_session_id", table_name="agent_memory_summaries")
    op.drop_table("agent_memory_summaries")
