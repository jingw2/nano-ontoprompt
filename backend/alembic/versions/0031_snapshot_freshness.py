"""Add immutable snapshot freshness/cursor/lineage pins.

`semantic_snapshots` gains `freshness_state` ("fresh" | "stale" | "unknown"),
`freshness_lag_seconds`, `source_cursor`, and `lineage_summary` — frozen at
materialization time by `materialize_refresh_snapshot` (Task 20), copied
verbatim from the successful `RefreshRun` that produced the snapshot's
inputs. A snapshot materialized without governed refresh context (the
pre-Task-20 `materialize_snapshot` call shape) defaults to "unknown" — the
Runtime policy layer must never treat an unknown-provenance snapshot as
silently fresh.

Note: numbered 0031 (not the plan's originally drafted 0026) because
0026-0030 were already taken by migrations landed since this task was
drafted; chains off the actual current head, 0030_runtime_plans (see that
migration's own note for the same numbering-drift precedent).

Revision ID: 0031_snapshot_freshness
Revises: 0030_runtime_plans
Create Date: 2026-08-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0031_snapshot_freshness"
down_revision = "0030_runtime_plans"
branch_labels = None
depends_on = None


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _json_server_default() -> sa.TextClause:
    if _dialect_name() == "mysql":
        return sa.text("(JSON_OBJECT())")
    return sa.text("'{}'")


def upgrade() -> None:
    op.add_column(
        "semantic_snapshots",
        sa.Column(
            "freshness_state", sa.String(10), nullable=False, server_default="unknown",
        ),
    )
    op.add_column("semantic_snapshots", sa.Column("freshness_lag_seconds", sa.Integer(), nullable=True))
    op.add_column("semantic_snapshots", sa.Column("source_cursor", sa.JSON(), nullable=True))
    op.add_column(
        "semantic_snapshots",
        sa.Column("lineage_summary", sa.JSON(), nullable=False, server_default=_json_server_default()),
    )
    op.create_check_constraint(
        "ck_semantic_snapshots_freshness_state",
        "semantic_snapshots",
        "freshness_state IN ('fresh', 'stale', 'unknown')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_semantic_snapshots_freshness_state", "semantic_snapshots", type_="check")
    op.drop_column("semantic_snapshots", "lineage_summary")
    op.drop_column("semantic_snapshots", "source_cursor")
    op.drop_column("semantic_snapshots", "freshness_lag_seconds")
    op.drop_column("semantic_snapshots", "freshness_state")
