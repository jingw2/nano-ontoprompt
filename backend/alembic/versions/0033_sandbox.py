"""Add immutable Sandbox simulation results.

`sandbox_simulations` (Task 22) persists one row per snapshot-backed
simulation computed by `simulate_action`
(`app.services.runtime.sandbox`) against an immutable `RuntimePlan`
(Task 15) and, when the plan is bound, a published `ManagedActionBinding`
(Task 21): expected row impact, a field-level before/after diff, the
rule/policy outcomes and precondition hashes it was computed from, and the
simulation's own expiry. Rows are append-only; the ORM-level guard in
`app.models.sandbox` rejects any update/delete on both SQLite (unit tests)
and PostgreSQL.

Note: numbered 0033 (not the plan's originally drafted 0028) because
0028-0032 were already taken by migrations landed since this task was
drafted; chains off the actual current head, 0032_managed_action_bindings
(see that migration's own note for the same numbering-drift precedent).

Revision ID: 0033_sandbox
Revises: 0032_managed_action_bindings
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0033_sandbox"
down_revision = "0032_managed_action_bindings"
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
        return f"REGEXP_LIKE({column}, '{UUID_PATTERN}', 'c')"
    if dialect == "sqlite":
        return f"{column} GLOB '{UUID_GLOB}'"
    return f"length({column}) = 36"


def _json_server_default(kind: str) -> sa.TextClause:
    """Return a JSON default accepted by each supported SQL dialect."""
    if _dialect_name() == "mysql":
        function = "JSON_ARRAY()" if kind == "array" else "JSON_OBJECT()"
        return sa.text(f"({function})")
    value = "[]" if kind == "array" else "{}"
    return sa.text(f"'{value}'")


def upgrade() -> None:
    op.create_table(
        "sandbox_simulations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "action_plan_id", sa.String(36), sa.ForeignKey("runtime_plans.id", ondelete="RESTRICT"),
            nullable=False, index=True,
        ),
        sa.Column(
            "semantic_snapshot_id", sa.String(36),
            sa.ForeignKey("semantic_snapshots.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "ontology_release_id", sa.String(36),
            sa.ForeignKey("ontology_releases.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "agent_id", sa.String(36),
            sa.ForeignKey("oauth_clients.id", ondelete="RESTRICT"), nullable=False, index=True,
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column(
            "managed_action_binding_id", sa.String(36),
            sa.ForeignKey("managed_action_bindings.managed_action_binding_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("binding_version", sa.String(40), nullable=True),
        sa.Column("expected_rows", sa.Integer(), nullable=False),
        sa.Column("before_after_diff", sa.JSON(), nullable=False, server_default=_json_server_default("object")),
        sa.Column("impact_summary", sa.JSON(), nullable=False, server_default=_json_server_default("object")),
        sa.Column("rule_outcome", sa.JSON(), nullable=False, server_default=_json_server_default("array")),
        sa.Column("policy_result", sa.JSON(), nullable=False, server_default=_json_server_default("object")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("precondition_hashes", sa.JSON(), nullable=False, server_default=_json_server_default("object")),
        sa.Column("status", sa.String(20), nullable=False, server_default="simulated"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(_uuid_check("id"), name="ck_sandbox_simulations_id_uuid"),
        sa.CheckConstraint(
            "(managed_action_binding_id IS NULL) = (binding_version IS NULL)",
            name="ck_sandbox_simulations_binding_pair",
        ),
    )


def downgrade() -> None:
    op.drop_table("sandbox_simulations")
