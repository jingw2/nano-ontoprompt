"""Add governed_turn_plans (Task 3, business-journey acceptance).

`governed_turn_plans` backs `app.services.runtime.turn_plans.
create_governed_plan_from_turn` / `decide_governed_plan` — a UI-facing
governed-action proposal created FROM an Agent turn's own persisted evidence
(`agent_tool_executions`/`agent_messages`), independent of `runtime_plans`
(which needs a materialized `SemanticSnapshot` + catalog `Action` a plain
conversational turn never produces) and independent of `agent_approvals`
(which is wired into that SAME turn's own dispatch/resume state machine).
See the model's own docstring in `app/models/runtime_execution.py` for the
full rationale.

Revision ID: 0035_governed_turn_plans
Revises: 0034_runtime_execution
Create Date: 2026-09-02

2026-09-16 fix: `decided_by_user_id` used a bare `sa.String` (no length)
where every sibling id column on this table uses `String(36)` — PostgreSQL
tolerates an unbounded VARCHAR silently, but this table's CREATE TABLE has
never once succeeded on the MySQL dialect fixture used by the runtime
cross-dialect integration tests (`sqlalchemy.exc.CompileError: VARCHAR
requires a length on dialect mysql`), so there is no already-migrated
MySQL database whose column type this edit could disagree with. Widened
to String(36) in place rather than via a follow-up ALTER, matching every
sibling id column.
"""

from alembic import op
import sqlalchemy as sa

revision = "0035_governed_turn_plans"
down_revision = "0034_runtime_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "governed_turn_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        # A real, independent approval identity (Important Finding #9) — never
        # aliased to `id`; minted alongside the plan in the same atomic create.
        sa.Column("approval_id", sa.String(36), nullable=False),
        sa.Column(
            "turn_id", sa.String(36), sa.ForeignKey("agent_turns.id", ondelete="RESTRICT"),
            nullable=False, index=True,
        ),
        sa.Column(
            "tool_execution_id", sa.String(36), sa.ForeignKey("agent_tool_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("branch", sa.String(20), nullable=False),
        sa.Column("target_fixture_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("target_before_hash", sa.String(64), nullable=False),
        sa.Column("target_after_hash", sa.String(64), nullable=False),
        sa.Column("receipt_id", sa.String(36), nullable=True),
        sa.Column("audit_event_id", sa.String(36), nullable=True),
        sa.Column("decided_by_user_id", sa.String(36), nullable=True),
        sa.Column("expiry", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("branch IN ('approved', 'rejected', 'expired')", name="ck_governed_turn_plans_branch"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')", name="ck_governed_turn_plans_status",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_governed_turn_plans_idempotency_key"),
        sa.UniqueConstraint("approval_id", name="uq_governed_turn_plans_approval_id"),
    )


def downgrade() -> None:
    op.drop_table("governed_turn_plans")
