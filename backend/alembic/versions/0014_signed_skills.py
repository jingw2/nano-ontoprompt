"""Signed Skill package/version/signature tables (P7C external tools).

A Skill package is the top-level identity; versions are immutable, signed
manifests; signatures are Ed25519 over the version's canonical hash (see
app/services/skills/__init__.py). agent_skill_bindings mirrors the P7A
external-tool binding pattern for Agent versions.

Revision ID: 0014_signed_skills
Revises: 0013_external_tool_alias_unique
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_signed_skills"
down_revision = "0013_external_tool_alias_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_skill_packages_status"),
    )
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("package_id", sa.String(36), sa.ForeignKey("skill_packages.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("canonical_hash", sa.String(64), nullable=False),
        sa.Column("approval_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("package_id", "version_no", name="uq_skill_versions_package_no"),
        sa.CheckConstraint("approval_status IN ('pending', 'approved', 'rejected')", name="ck_skill_versions_approval"),
    )
    op.create_table(
        "skill_signatures",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("skill_versions.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("algorithm", sa.String(20), nullable=False, server_default="ed25519"),
        sa.Column("public_key_hex", sa.String(128), nullable=False),
        sa.Column("signature_hex", sa.String(256), nullable=False),
        sa.Column("signer_identity", sa.String(200), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "agent_skill_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_version_id", sa.String(36), sa.ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("skill_version_id", sa.String(36), sa.ForeignKey("skill_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("alias", sa.String(55), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("agent_version_id", "alias", name="uq_asb_version_alias"),
    )


def downgrade() -> None:
    op.drop_table("agent_skill_bindings")
    op.drop_table("skill_signatures")
    op.drop_table("skill_versions")
    op.drop_table("skill_packages")
