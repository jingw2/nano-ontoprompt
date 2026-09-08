"""Unique (agent_version_id, alias) on agent_external_tool_bindings (P7A).

Two external-tool bindings sharing an alias on the same immutable Agent
version would produce two identically-named LangGraph tools (Task 7 of the
P7A plan derives the tool name from the alias) — reject that at write time
instead of silently colliding at runtime.

Revision ID: 0013_external_tool_alias_unique
Revises: 0012_tool_provider_kind
Create Date: 2026-08-17
"""
from alembic import op

revision = "0013_external_tool_alias_unique"
down_revision = "0012_tool_provider_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_aetb_version_alias", "agent_external_tool_bindings", ["agent_version_id", "alias"])


def downgrade() -> None:
    op.drop_constraint("uq_aetb_version_alias", "agent_external_tool_bindings", type_="unique")
