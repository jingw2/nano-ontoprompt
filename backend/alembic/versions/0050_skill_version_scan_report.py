"""Add scan_report to skill_versions.

Revision ID: 0050_skill_version_scan_report
Revises: 0049_tool_provider_kind_browser_use
Create Date: 2026-09-14

A user-uploaded .md/.zip skill (see app/services/skills/upload.py) is
security-scanned before it is auto-signed and auto-approved. `scan_report`
records what the scan checked and found (an empty list means clean) so an
admin reviewing an already-approved skill can see why it was trusted —
manually authored skills (created via the JSON manifest+signature form)
leave this NULL.
"""
from alembic import op
import sqlalchemy as sa

revision = "0050_skill_version_scan_report"
down_revision = "0049_tool_provider_kind_browser_use"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_versions",
        sa.Column("scan_report", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("skill_versions", "scan_report")
