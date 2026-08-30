"""Add governed runtime execution, reconciliation, and durable approval.

`runtime_executions` (Task 26) is the execution fence: at most one row per
`RuntimePlan` (a `UNIQUE` constraint on `plan_id`), inserted `PENDING` before
a `ManagedRowWriter` (Task 24/25) is ever called and updated exactly once to
a terminal `status`. `runtime_reconciliation_cases` records an `UNKNOWN`
(ambiguous) outcome for human review — a distinct table from the pre-existing
P5C `agent_reconciliation_cases`, a different domain (Agent tool-call
executions, not `RuntimePlan`s). `runtime_execution_approvals` durably
persists what Task 23's `approve_exact_plan` already verifies in memory but
never writes to disk, so `execute_plan` has a real fact to check for a
`HUMAN_APPROVED` plan.

Note: numbered 0034 (not the plan's originally drafted 0029) because
0029-0033 were already taken by migrations landed since this task was
drafted; chains off the actual current head, 0033_sandbox (see that
migration's own note for the same numbering-drift precedent).

Revision ID: 0034_runtime_execution
Revises: 0033_sandbox
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0034_runtime_execution"
down_revision = "0033_sandbox"
branch_labels = None
depends_on = None

UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
UUID_GLOB = (
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
    "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]"
)


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _uuid_check(column: str) -> str:
    dialect = _dialect_name()
    if dialect == "postgresql":
        return f"{column} ~ '{UUID_PATTERN}'"
    if dialect == "mysql":
        return f"BINARY {column} REGEXP '{UUID_PATTERN}'"
    if dialect == "sqlite":
        return f"{column} GLOB '{UUID_GLOB}'"
    return f"length({column}) = 36"


def _json_server_default(kind: str) -> sa.TextClause:
    if _dialect_name() == "mysql":
        function = "JSON_ARRAY()" if kind == "array" else "JSON_OBJECT()"
        return sa.text(f"({function})")
    value = "[]" if kind == "array" else "{}"
    return sa.text(f"'{value}'")


def upgrade() -> None:
    op.create_table(
        "runtime_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "plan_id", sa.String(36), sa.ForeignKey("runtime_plans.id", ondelete="RESTRICT"),
            nullable=False, index=True,
        ),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("execution_class", sa.String(20), nullable=False),
        sa.Column("dialect", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("writer_receipt", sa.JSON(), nullable=True),
        sa.Column("audit_id", sa.String(36), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("reconciliation_case_id", sa.String(36), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(_uuid_check("id"), name="ck_runtime_executions_id_uuid"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')", name="ck_runtime_executions_status",
        ),
        sa.CheckConstraint(
            "execution_class IN ('AUTOMATIC', 'HUMAN_APPROVED')", name="ck_runtime_executions_execution_class",
        ),
        sa.CheckConstraint("dialect IN ('postgresql', 'mysql')", name="ck_runtime_executions_dialect"),
        sa.UniqueConstraint("plan_id", name="uq_runtime_executions_plan_id"),
    )

    op.create_table(
        "runtime_reconciliation_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "execution_id", sa.String(36), sa.ForeignKey("runtime_executions.id", ondelete="RESTRICT"),
            nullable=False, index=True,
        ),
        sa.Column(
            "plan_id", sa.String(36), sa.ForeignKey("runtime_plans.id", ondelete="RESTRICT"),
            nullable=False, index=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("unknown_reason", sa.String(500), nullable=False),
        sa.Column("observed_effect", sa.JSON(), nullable=False, server_default=_json_server_default("object")),
        sa.Column("next_action", sa.String(100), nullable=False, server_default="human_review"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(_uuid_check("id"), name="ck_runtime_reconciliation_cases_id_uuid"),
        sa.CheckConstraint(
            "status IN ('open', 'resolved_succeeded', 'resolved_failed')",
            name="ck_runtime_reconciliation_cases_status",
        ),
    )

    op.create_table(
        "runtime_execution_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "plan_id", sa.String(36), sa.ForeignKey("runtime_plans.id", ondelete="RESTRICT"),
            nullable=False, index=True,
        ),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("approver_agent_id", sa.String(36), nullable=False),
        sa.Column("approver_user_id", sa.String(36), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expiry", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(_uuid_check("id"), name="ck_runtime_execution_approvals_id_uuid"),
    )


def downgrade() -> None:
    op.drop_table("runtime_execution_approvals")
    op.drop_table("runtime_reconciliation_cases")
    op.drop_table("runtime_executions")
