"""Agent idempotency-key persistence (I-BACKEND serial packet).

Revision ID: 0007_agent_idempotency
Revises: 0006_agent_runtime

I-BACKEND owns the Section 12 idempotency contract: `Idempotency-Key`
(16-128 printable ASCII) persisted with actor/route/canonical request hash
for 24 hours; same key with a different hash is `409 IDEMPOTENCY_KEY_REUSED`.
The table is deliberately NOT registered in the ORM metadata (E0-DB's exact
table-set contract stays untouched); `app.services.idempotency` accesses it
with raw SQL.
"""
from alembic import op
import sqlalchemy as sa


revision = "0007_agent_idempotency"
down_revision = "0006_agent_runtime"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_idempotency_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("route", sa.String(512), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_agent_idempotency_actor_key"),
    )
    op.create_index(
        "ix_agent_idempotency_keys_expires_at",
        "agent_idempotency_keys",
        ["expires_at"],
    )


def downgrade():
    op.drop_index("ix_agent_idempotency_keys_expires_at", table_name="agent_idempotency_keys")
    op.drop_table("agent_idempotency_keys")
