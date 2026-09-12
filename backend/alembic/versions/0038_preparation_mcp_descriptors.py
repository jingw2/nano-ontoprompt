"""Persist the granted MCP descriptor ids on business_journey_preparations.

Revision ID: 0038_preparation_mcp_descriptors
Revises: 0037_curated_review_pipeline_run
Create Date: 2026-09-05

2026-09-12 fix: the literal `server_default="[]"` compiles to a bare
string default on every dialect; MySQL additionally rejects any literal
DEFAULT on a JSON column outright (`Error 1101: BLOB, TEXT, GEOMETRY or
JSON column can't have a default value`). Switched to the dialect-aware
default already established by 0030/0032/0033/0034/0031.
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_preparation_mcp_descriptors"
down_revision = "0037_curated_review_pipeline_run"
branch_labels = None
depends_on = None


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _json_server_default(kind: str) -> sa.TextClause:
    """Return a JSON default accepted by each supported SQL dialect."""
    if _dialect_name() == "mysql":
        function = "JSON_ARRAY()" if kind == "array" else "JSON_OBJECT()"
        return sa.text(f"({function})")
    value = "[]" if kind == "array" else "{}"
    return sa.text(f"'{value}'")


def upgrade() -> None:
    op.add_column(
        "business_journey_preparations",
        sa.Column("mcp_descriptor_ids", sa.JSON(), nullable=False, server_default=_json_server_default("array")),
    )


def downgrade() -> None:
    op.drop_column("business_journey_preparations", "mcp_descriptor_ids")
