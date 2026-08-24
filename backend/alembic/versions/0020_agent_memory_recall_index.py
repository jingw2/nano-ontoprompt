"""Lexical search index for long-term memory recall (P6B-2b, Section 11).

Adds a generated tsvector column over agent_memories.display_text and a GIN
index on it, consumed by the recall algorithm's lexical channel
(ts_rank_cd). Uses PostgreSQL's built-in 'simple' text-search configuration
— no CJK segmentation extension exists in this codebase; Chinese-language
memory text will not be meaningfully word-segmented by this channel. That
is a documented, accepted limitation, not something this migration attempts
to solve.

Revision ID: 0020_agent_memory_recall_index
Revises: 0019_agent_memory_long_term
Create Date: 2026-08-24
"""
from alembic import op

revision = "0020_agent_memory_recall_index"
down_revision = "0019_agent_memory_long_term"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_memories ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', display_text)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_agent_memories_search_vector ON agent_memories USING GIN (search_vector)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_memories_search_vector")
    op.execute("ALTER TABLE agent_memories DROP COLUMN IF EXISTS search_vector")
