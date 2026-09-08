"""Scope runtime business-journey model-call slots to an Agent Turn.

Revision ID: 0041_business_journey_model_call_turn_scope
Revises: 0040_agent_session_title
Create Date: 2026-09-07

The preparation call has no Agent Turn, so it receives the stable internal
``__preparation__`` scope.  Existing runtime rows are retained under a
per-row legacy scope; they remain historical evidence and cannot collide
with any new real turn id.
"""
from alembic import op
import sqlalchemy as sa


revision = "0041_business_journey_model_call_turn_scope"
down_revision = "0040_agent_session_title"
branch_labels = None
depends_on = None

_PREPARATION_SCOPE = "__preparation__"
_LEGACY_SCOPE_PREFIX = "__legacy__:"
_OLD_CONSTRAINT = "uq_business_journey_model_call_slot"
_NEW_CONSTRAINT = "uq_business_journey_model_call_turn_slot"


def upgrade() -> None:
    op.add_column(
        "business_journey_model_calls",
        sa.Column("turn_id", sa.String(200), nullable=True),
    )
    bind = op.get_bind()
    legacy_scope = "CONCAT(:legacy_prefix, id)" if bind.dialect.name == "mysql" else ":legacy_prefix || id"
    bind.execute(sa.text(
        "UPDATE business_journey_model_calls "
        "SET turn_id = CASE WHEN phase = 'preparation' "
        f"THEN :preparation_scope ELSE {legacy_scope} END "
        "WHERE turn_id IS NULL"
    ), {"preparation_scope": _PREPARATION_SCOPE, "legacy_prefix": _LEGACY_SCOPE_PREFIX})

    if bind.dialect.name == "sqlite":
        # SQLite cannot alter nullability or constraints in place.  This
        # table has no inbound foreign keys, so the batch recreation is safe
        # and keeps the migration executable on the unit dialect too.
        with op.batch_alter_table("business_journey_model_calls", recreate="always") as batch:
            batch.drop_constraint(_OLD_CONSTRAINT, type_="unique")
            batch.alter_column(
                "turn_id", existing_type=sa.String(200), nullable=False,
            )
            batch.create_unique_constraint(
                _NEW_CONSTRAINT,
                ["run_id", "journey_id", "turn_id", "logical_call_index"],
            )
        return

    op.drop_constraint(_OLD_CONSTRAINT, "business_journey_model_calls", type_="unique")
    op.alter_column(
        "business_journey_model_calls", "turn_id",
        existing_type=sa.String(200), nullable=False,
    )
    op.create_unique_constraint(
        _NEW_CONSTRAINT, "business_journey_model_calls",
        ["run_id", "journey_id", "turn_id", "logical_call_index"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(sa.text(
        "SELECT 1 FROM business_journey_model_calls "
        "GROUP BY run_id, journey_id, logical_call_index "
        "HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate is not None:
        raise RuntimeError(
            "CANNOT_DOWNGRADE_BUSINESS_JOURNEY_MODEL_CALL_TURN_SCOPE: "
            "multiple turn-scoped slots would violate the legacy uniqueness contract"
        )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("business_journey_model_calls", recreate="always") as batch:
            batch.drop_constraint(_NEW_CONSTRAINT, type_="unique")
            batch.drop_column("turn_id")
            batch.create_unique_constraint(
                _OLD_CONSTRAINT, ["run_id", "journey_id", "logical_call_index"],
            )
        return

    op.drop_constraint(_NEW_CONSTRAINT, "business_journey_model_calls", type_="unique")
    op.drop_column("business_journey_model_calls", "turn_id")
    op.create_unique_constraint(
        _OLD_CONSTRAINT, "business_journey_model_calls",
        ["run_id", "journey_id", "logical_call_index"],
    )
