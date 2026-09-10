"""Replace ad-hoc formula/execution_rule/function_code fields with the
Palantir Ontology Functions/Action model.

Revision ID: 0042_logic_action_palantir_model
Revises: 0041_business_journey_model_call_turn_scope
Create Date: 2026-09-09

LogicRule.formula (a freeform "IF ... THEN ..." string) is replaced by
function_type (derived_property|aggregation|complex_edit|external_query) +
definition (the expression/description for that type); the old formula text
is best-effort copied into definition since it remains a valid free-text
description even though the field it now sits in is more strictly typed.

Action.execution_rule + Action.function_code (a freeform trigger description
plus an LLM-guessed Python function that was never actually executed) are
replaced by four structured components: parameters, rules,
submission_criteria, side_effects. There is no safe automatic mapping from
the old freeform text into these typed lists, so existing actions start with
empty lists rather than a guessed structure.
"""
from alembic import op
import sqlalchemy as sa


revision = "0042_logic_action_palantir_model"
down_revision = "0041_business_journey_model_call_turn_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("logic_rules", sa.Column("function_type", sa.String(30), nullable=True))
    op.add_column("logic_rules", sa.Column("definition", sa.Text(), nullable=True))
    op.execute("UPDATE logic_rules SET definition = formula WHERE formula IS NOT NULL")
    op.drop_column("logic_rules", "formula")

    op.add_column("actions", sa.Column("parameters", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("actions", sa.Column("rules", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("actions", sa.Column("submission_criteria", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("actions", sa.Column("side_effects", sa.JSON(), nullable=False, server_default="[]"))
    op.drop_column("actions", "execution_rule")
    op.drop_column("actions", "function_code")


def downgrade() -> None:
    op.add_column("actions", sa.Column("function_code", sa.Text(), nullable=True))
    op.add_column("actions", sa.Column("execution_rule", sa.Text(), nullable=True))
    op.drop_column("actions", "side_effects")
    op.drop_column("actions", "submission_criteria")
    op.drop_column("actions", "rules")
    op.drop_column("actions", "parameters")

    op.add_column("logic_rules", sa.Column("formula", sa.Text(), nullable=True))
    op.execute("UPDATE logic_rules SET formula = definition WHERE definition IS NOT NULL")
    op.drop_column("logic_rules", "definition")
    op.drop_column("logic_rules", "function_type")
