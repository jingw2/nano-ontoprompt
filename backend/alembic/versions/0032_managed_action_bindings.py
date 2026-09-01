"""Add managed action bindings and pin the writable ActionPlan's binding FK.

`managed_action_bindings` (Task 21) is the governance layer that makes a
writable `Action` (Task 15's `create_action_plan`) concrete: a versioned,
published/draft/revoked binding to a fixed connection identity, SQL dialect,
schema/table, primary-key columns, an allowlisted set of writable columns, a
version-column optimistic-concurrency precondition, a typed parameter
schema, and `secret_ref` — a vault reference only, never a secret value.
`runtime_plans.managed_action_binding_id` (added ahead of this task by
0030_runtime_plans) gets a real foreign key now that the table it points to
exists, plus a pairing check so a plan can never carry a binding id with no
pinned `binding_version` or vice versa.

Note: numbered 0032 (not the plan's originally drafted 0027) because
0027-0031 were already taken by migrations landed since this task was
drafted; chains off the actual current head, 0031_snapshot_freshness (see
that migration's own note for the same numbering-drift precedent).

Revision ID: 0032_managed_action_bindings
Revises: 0031_snapshot_freshness
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0032_managed_action_bindings"
down_revision = "0031_snapshot_freshness"
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
        return f"{column} REGEXP '{UUID_PATTERN}'"
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
        "managed_action_bindings",
        sa.Column("managed_action_binding_id", sa.String(36), primary_key=True),
        sa.Column(
            "action_id", sa.String(36), sa.ForeignKey("actions.id", ondelete="RESTRICT"),
            nullable=False, index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "connection_id", sa.String(36), sa.ForeignKey("v2_connections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("connection_target_identity", sa.String(500), nullable=False),
        sa.Column("dialect", sa.String(20), nullable=False),
        sa.Column("schema_name", sa.String(200), nullable=False),
        sa.Column("table_name", sa.String(200), nullable=False),
        sa.Column("primary_key_columns", sa.JSON(), nullable=False, server_default=_json_server_default("array")),
        sa.Column("writable_columns", sa.JSON(), nullable=False, server_default=_json_server_default("array")),
        sa.Column("version_column", sa.String(200), nullable=False),
        sa.Column("parameter_schema", sa.JSON(), nullable=False, server_default=_json_server_default("object")),
        sa.Column("secret_ref", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(_uuid_check("managed_action_binding_id"), name="ck_managed_action_bindings_id_uuid"),
        sa.CheckConstraint("status IN ('draft', 'published', 'revoked')", name="ck_managed_action_bindings_status"),
        sa.CheckConstraint("dialect IN ('postgresql', 'mysql')", name="ck_managed_action_bindings_dialect"),
        sa.UniqueConstraint("action_id", "version", name="uq_managed_action_bindings_action_version"),
    )
    op.create_foreign_key(
        "fk_runtime_plans_managed_action_binding_id",
        "runtime_plans", "managed_action_bindings",
        ["managed_action_binding_id"], ["managed_action_binding_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_runtime_plans_binding_pair",
        "runtime_plans",
        "(managed_action_binding_id IS NULL) = (binding_version IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_runtime_plans_binding_pair", "runtime_plans", type_="check")
    op.drop_constraint("fk_runtime_plans_managed_action_binding_id", "runtime_plans", type_="foreignkey")
    op.drop_table("managed_action_bindings")
