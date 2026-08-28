"""Reject SemanticSnapshot inserts whose ontology release is not published.

Mirrors the SNAPSHOT_INPUT_NOT_GOVERNED trigger added in 0027_semantic_snapshot:
that migration enforces that every SemanticSnapshotInput points at a
completed, governed pipeline run. This migration adds the symmetric,
dialect-aware database trigger on `semantic_snapshots` itself so that
`ontology_release_id` can only ever reference an OntologyRelease with
status = 'published' -- on the ORM path (see
app/models/semantic_snapshot.py::_validate_snapshot_release_published) and on
raw SQL that bypasses the ORM.

Revision ID: 0028_snapshot_release_published
Revises: 0027_semantic_snapshot
Create Date: 2026-08-28
"""

from alembic import op


revision = "0028_snapshot_release_published"
down_revision = "0027_semantic_snapshot"
branch_labels = None
depends_on = None


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _create_postgresql_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION validate_semantic_snapshot_release() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE release_status varchar;
        BEGIN
          SELECT status INTO release_status
            FROM ontology_releases WHERE id = NEW.ontology_release_id;
          IF release_status IS DISTINCT FROM 'published' THEN
            RAISE EXCEPTION 'SNAPSHOT_RELEASE_NOT_PUBLISHED';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER semantic_snapshots_validate_release
        BEFORE INSERT ON semantic_snapshots
        FOR EACH ROW EXECUTE FUNCTION validate_semantic_snapshot_release()
        """
    )


def _create_mysql_guard() -> None:
    op.execute(
        """
        CREATE TRIGGER semantic_snapshots_validate_release
        BEFORE INSERT ON semantic_snapshots FOR EACH ROW
        BEGIN
          IF (SELECT status FROM ontology_releases WHERE id = NEW.ontology_release_id) <> 'published' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SNAPSHOT_RELEASE_NOT_PUBLISHED';
          END IF;
        END
        """
    )


def _create_sqlite_guard() -> None:
    op.execute(
        """
        CREATE TRIGGER semantic_snapshots_validate_release
        BEFORE INSERT ON semantic_snapshots
        WHEN NOT EXISTS (
               SELECT 1 FROM ontology_releases
                WHERE id = NEW.ontology_release_id
                  AND status = 'published'
             )
        BEGIN
          SELECT RAISE(ABORT, 'SNAPSHOT_RELEASE_NOT_PUBLISHED');
        END
        """
    )


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        _create_postgresql_guard()
    elif dialect == "mysql":
        _create_mysql_guard()
    elif dialect == "sqlite":
        _create_sqlite_guard()


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS semantic_snapshots_validate_release ON semantic_snapshots")
        op.execute("DROP FUNCTION IF EXISTS validate_semantic_snapshot_release()")
    elif dialect == "mysql":
        op.execute("DROP TRIGGER IF EXISTS semantic_snapshots_validate_release")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS semantic_snapshots_validate_release")
