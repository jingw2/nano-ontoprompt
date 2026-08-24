"""P6B-2a: long-term Agent memory write path — schema foundation.

Adds a predicate registry (the fixed taxonomy long-term memories are
namespaced under), the `agent_memories` table itself (namespaced by
security domain/Agent/user, with a partial-unique dedup key scoped to
`status = 'active'` so historical/deleted/conflicted rows never block a
fresh write), a revision log for user corrections, a consent ledger,
a conflict-tracking table, and two outbox tables: `agent_memory_vector_outbox`
(consumed by P6B-2b's embedding/Chroma pipeline — not implemented here) and
`agent_memory_extraction_outbox` (consumed by this plan's own extraction
service in a later task).

Revision ID: 0019_agent_memory_long_term
Revises: 0018_agent_memory_short_term
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0019_agent_memory_long_term"
down_revision = "0018_agent_memory_short_term"
branch_labels = None
depends_on = None

# five starter predicates, three multi/two single — enough for later tasks'
# tests to exercise both cardinalities without inventing a real-world
# taxonomy this plan doesn't own
PREDICATE_SEEDS = (
    ("user.name", "single"),
    ("user.role", "single"),
    ("user.preference", "multi"),
    ("user.fact", "multi"),
    ("user.goal", "multi"),
)


def upgrade() -> None:
    op.create_table(
        "agent_memory_predicate_registry",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("predicate", sa.String(100), nullable=False, unique=True),
        sa.Column("cardinality", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("cardinality IN ('single', 'multi')", name="ck_agent_memory_predicate_registry_cardinality"),
    )

    conn = op.get_bind()
    for predicate, cardinality in PREDICATE_SEEDS:
        conn.execute(
            sa.text(
                "INSERT INTO agent_memory_predicate_registry (id, predicate, cardinality, created_at) "
                "VALUES (gen_random_uuid()::text, :predicate, :cardinality, now())"
            ),
            {"predicate": predicate, "cardinality": cardinality},
        )

    op.create_table(
        "agent_memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("security_domain_id", sa.String(36),
                  sa.ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("agent_id", sa.String(36),
                  sa.ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.String(36),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("subject_key", sa.String(200), nullable=False),
        sa.Column("predicate", sa.String(100),
                  sa.ForeignKey("agent_memory_predicate_registry.predicate", ondelete="RESTRICT"), nullable=False),
        sa.Column("canonical_value", JSONB, nullable=False),
        sa.Column("canonical_value_hash", sa.String(64), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("consent_basis", sa.String(30), nullable=False),
        sa.Column("source_spans", JSONB, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("embedding_model_version", sa.String(100), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('semantic', 'episodic')", name="ck_agent_memories_kind"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_agent_memories_confidence"),
        sa.CheckConstraint(
            "consent_basis IN ('explicit_statement', 'explicit_confirmation')",
            name="ck_agent_memories_consent_basis",
        ),
        sa.CheckConstraint(
            "status IN ('pending_confirmation', 'active', 'conflicted', 'deleted')",
            name="ck_agent_memories_status",
        ),
    )
    # partial unique index — the spec's "unique active" dedup constraint:
    # multiple historical/deleted/conflicted rows may share a dedup key, but
    # at most one 'active' row may.
    op.create_index(
        "uq_agent_memories_active_dedup_key",
        "agent_memories",
        ["security_domain_id", "agent_id", "user_id", "subject_key", "predicate", "canonical_value_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "agent_memory_consents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("security_domain_id", sa.String(36),
                  sa.ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("agent_id", sa.String(36),
                  sa.ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.String(36),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("consent_basis", sa.String(30), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "consent_basis IN ('explicit_statement', 'explicit_confirmation')",
            name="ck_agent_memory_consents_consent_basis",
        ),
    )

    op.create_table(
        "agent_memory_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("memory_id", sa.String(36),
                  sa.ForeignKey("agent_memories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("canonical_value", JSONB, nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(), nullable=False),
        sa.Column("consent_basis", sa.String(30), nullable=False),
        sa.Column("source_spans", JSONB, nullable=False),
        sa.Column("consent_id", sa.String(36),
                  sa.ForeignKey("agent_memory_consents.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("memory_id", "revision_no", name="uq_agent_memory_revisions_memory_revision"),
    )

    op.create_table(
        "agent_memory_conflicts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("security_domain_id", sa.String(36),
                  sa.ForeignKey("security_domains.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("agent_id", sa.String(36),
                  sa.ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.String(36),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("subject_key", sa.String(200), nullable=False),
        sa.Column("predicate", sa.String(100),
                  sa.ForeignKey("agent_memory_predicate_registry.predicate", ondelete="RESTRICT"), nullable=False),
        sa.Column("memory_id_a", sa.String(36),
                  sa.ForeignKey("agent_memories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("memory_id_b", sa.String(36),
                  sa.ForeignKey("agent_memories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("resolved_by_revision_id", sa.String(36),
                  sa.ForeignKey("agent_memory_revisions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_agent_memory_conflicts_status"),
    )

    op.create_table(
        "agent_memory_vector_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("memory_id", sa.String(36),
                  sa.ForeignKey("agent_memories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("event_type IN ('upsert', 'delete')", name="ck_agent_memory_vector_outbox_event_type"),
        sa.CheckConstraint("state IN ('pending', 'applied')", name="ck_agent_memory_vector_outbox_state"),
    )
    op.create_index("ix_agent_memory_vector_outbox_state", "agent_memory_vector_outbox", ["state"])

    op.create_table(
        "agent_memory_extraction_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("turn_id", sa.String(36),
                  sa.ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("session_id", sa.String(36),
                  sa.ForeignKey("agent_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'applied', 'skipped')",
            name="ck_agent_memory_extraction_outbox_state",
        ),
    )
    op.create_index("ix_agent_memory_extraction_outbox_state", "agent_memory_extraction_outbox", ["state"])


def downgrade() -> None:
    op.drop_index("ix_agent_memory_extraction_outbox_state", table_name="agent_memory_extraction_outbox")
    op.drop_table("agent_memory_extraction_outbox")
    op.drop_index("ix_agent_memory_vector_outbox_state", table_name="agent_memory_vector_outbox")
    op.drop_table("agent_memory_vector_outbox")
    op.drop_table("agent_memory_conflicts")
    op.drop_table("agent_memory_revisions")
    op.drop_table("agent_memory_consents")
    op.drop_index("uq_agent_memories_active_dedup_key", table_name="agent_memories")
    op.drop_table("agent_memories")
    op.drop_table("agent_memory_predicate_registry")
