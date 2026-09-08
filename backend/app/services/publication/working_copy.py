"""OntologyWorkingCopyService: one transaction owns every schema mutation.

Each successful working-schema/tool mutation goes through `mutate`, which
locks the Ontology row, runs the mutation callback, increments
`working_revision`, sets `is_dirty` (true only when a release exists), and
enqueues the audit outbox — all in one transaction.  Rollback restores
revision/dirty/audit state.  `upgrade_working_copy_foundation()` adds the
`is_dirty` column to revision 0003 (P1B-CLOSURE; sanctioned 0003 wiring).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.services.publication.dirty_hooks import mark_ontology_dirty


class WorkingCopyError(Exception):
    pass


class OntologyWorkingCopyService:
    @staticmethod
    def mutate(db: Session, *, ontology_id: str, actor_id: str, operation: str, callback):
        """Own the mutation, working-revision increment, dirty transition, and audit."""
        # SQLite (unit harness only) has no FOR UPDATE; real locking is PostgreSQL.
        lock = " FOR UPDATE" if db.get_bind().dialect.name != "sqlite" else ""
        row = db.execute(
            sa.text(
                "SELECT id, security_domain_id, latest_published_release_id "
                "FROM ontology_projects WHERE id = :id" + lock
            ),
            {"id": ontology_id},
        ).mappings().one_or_none()
        if row is None:
            raise WorkingCopyError("ONTOLOGY_NOT_FOUND")
        try:
            result = callback()
            mark_ontology_dirty(
                db,
                ontology_id=ontology_id,
                actor_id=actor_id,
                operation=operation,
                security_domain_id=row["security_domain_id"],
                dirty=row["latest_published_release_id"] is not None,
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise


def upgrade_working_copy_foundation() -> None:
    op.add_column(
        "ontology_projects",
        sa.Column("is_dirty", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade_working_copy_foundation() -> None:
    op.drop_column("ontology_projects", "is_dirty")
