"""Role unification + immutable LLM model behavior versions.

Revision ID: 0004_roles_model_versions
Revises: 0003_publication_governance
"""

from alembic import op

from app.services.model_version import (
    downgrade_legacy_llm_rows,
    downgrade_model_versions_foundation,
    downgrade_role_unification,
    upgrade_legacy_llm_rows,
    upgrade_model_versions_foundation,
    upgrade_role_unification,
)


revision = "0004_roles_model_versions"
down_revision = "0003_publication_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_role_unification()
    upgrade_model_versions_foundation()
    upgrade_legacy_llm_rows()


def downgrade() -> None:
    downgrade_legacy_llm_rows()
    downgrade_model_versions_foundation()
    downgrade_role_unification()
