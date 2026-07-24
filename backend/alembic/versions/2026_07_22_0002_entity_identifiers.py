"""add entity identifier columns (name_abbr, snomed_id, canonical_id)

baseline 0001 缺失 entity 的三个标识符列，此前由 main.py 启动时 ALTER TABLE 补齐；
现迁移至 Alembic，使 schema 演进有统一迁移路径。

Revision ID: 0002_entity_identifiers
Revises: 0001_full_baseline
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_entity_identifiers"
down_revision = "0001_full_baseline"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("entities", "name_abbr"):
        op.add_column("entities", sa.Column("name_abbr", sa.String(length=50), nullable=True))
    if not _has_column("entities", "snomed_id"):
        op.add_column("entities", sa.Column("snomed_id", sa.String(length=50), nullable=True))
    if not _has_column("entities", "canonical_id"):
        op.add_column("entities", sa.Column("canonical_id", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("entities") as batch_op:
        batch_op.drop_column("canonical_id")
        batch_op.drop_column("snomed_id")
        batch_op.drop_column("name_abbr")
