"""Add immutable snapshot-pinned action plans.

`runtime_plans` persists one row per non-writing proposal produced by
`RuntimeService.create_action_plan` (Task 15): the snapshot/release it is
pinned to, the verified Agent/user principals, the evidence and rule
outcomes it was computed from, its integrity pins (`before_image_hash`,
`version_hash`, `precondition_hashes`, `plan_hash`), and its expiry. Rows are
append-only; the ORM-level guard in `app.models.runtime_plan` rejects any
update/delete on both SQLite (unit tests) and PostgreSQL.

Note: numbered 0030 (not the plan's originally drafted 0025) because
0025-0029 were already taken by migrations landed since this task was
drafted; chains off the actual current head, 0029_runtime_identity.

Revision ID: 0030_runtime_plans
Revises: 0029_runtime_identity
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0030_runtime_plans"
down_revision = "0029_runtime_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "semantic_snapshot_id", sa.String(36),
            sa.ForeignKey("semantic_snapshots.id", ondelete="RESTRICT"), nullable=False, index=True,
        ),
        sa.Column(
            "ontology_release_id", sa.String(36),
            sa.ForeignKey("ontology_releases.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "agent_id", sa.String(36),
            sa.ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False, index=True,
        ),
        sa.Column("user_id", sa.String, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("action_id", sa.String, sa.ForeignKey("actions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("input_facts", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("evidence_citations", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("rule_outcomes", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("managed_action_binding_id", sa.String(36), nullable=True),
        sa.Column("binding_version", sa.String(40), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("target_key", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("before_image_hash", sa.String(64), nullable=False),
        sa.Column("version_hash", sa.String(64), nullable=False),
        sa.Column("predicted_diff", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("impact_scope", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("risk_classification", sa.String(40), nullable=False),
        sa.Column("policy_decision", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("precondition_hashes", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("expiry", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="ck_runtime_plans_id_uuid",
        ),
        sa.CheckConstraint("length(plan_hash) = 64", name="ck_runtime_plans_plan_hash"),
        sa.CheckConstraint("length(before_image_hash) = 64", name="ck_runtime_plans_before_image_hash"),
        sa.CheckConstraint("length(version_hash) = 64", name="ck_runtime_plans_version_hash"),
    )


def downgrade() -> None:
    op.drop_table("runtime_plans")
