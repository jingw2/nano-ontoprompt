"""Add immutable semantic snapshots and governed input lineage.

Revision ID: 0027_semantic_snapshot
Revises: 0026_refresh_event_inbox
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_semantic_snapshot"
down_revision = "0026_refresh_event_inbox"
branch_labels = None
depends_on = None


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _drop_release_immutability_guard() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS ontology_releases_immutable ON ontology_releases")
    elif dialect == "mysql":
        op.execute("DROP TRIGGER IF EXISTS ontology_releases_immutable_update")
        op.execute("DROP TRIGGER IF EXISTS ontology_releases_immutable_delete")


def _restore_release_immutability_guard() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_ontology_release_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
              RAISE EXCEPTION 'RELEASE_IMMUTABLE';
            END;
            $$
            """
        )
        op.execute(
            "CREATE TRIGGER ontology_releases_immutable BEFORE UPDATE OR DELETE "
            "ON ontology_releases FOR EACH ROW EXECUTE FUNCTION reject_ontology_release_mutation()"
        )
    elif dialect == "mysql":
        op.execute(
            """
            CREATE TRIGGER ontology_releases_immutable_update
            BEFORE UPDATE ON ontology_releases FOR EACH ROW
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'RELEASE_IMMUTABLE'
            """
        )
        op.execute(
            """
            CREATE TRIGGER ontology_releases_immutable_delete
            BEFORE DELETE ON ontology_releases FOR EACH ROW
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'RELEASE_IMMUTABLE'
            """
        )


def _create_postgresql_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_semantic_snapshot_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'SEMANTIC_SNAPSHOT_IMMUTABLE';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER semantic_snapshots_immutable
        BEFORE UPDATE OR DELETE ON semantic_snapshots
        FOR EACH ROW EXECUTE FUNCTION reject_semantic_snapshot_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER semantic_snapshot_inputs_immutable
        BEFORE UPDATE OR DELETE ON semantic_snapshot_inputs
        FOR EACH ROW EXECUTE FUNCTION reject_semantic_snapshot_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_semantic_snapshot_input() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE run_status varchar; run_finished_at timestamptz;
                output_dataset_version_id varchar; has_lineage boolean;
        BEGIN
          SELECT status, finished_at, dataset_version_id
            INTO run_status, run_finished_at, output_dataset_version_id
            FROM v2_pipeline_runs WHERE id = NEW.pipeline_run_id;
          SELECT EXISTS(
            SELECT 1 FROM pipeline_run_inputs
             WHERE pipeline_run_id = NEW.pipeline_run_id
               AND dataset_version_id = NEW.dataset_version_id
          ) INTO has_lineage;
          IF run_status IS DISTINCT FROM 'success'
             OR run_finished_at IS NULL
             OR output_dataset_version_id IS NULL
             OR NOT has_lineage THEN
            RAISE EXCEPTION 'SNAPSHOT_INPUT_NOT_GOVERNED';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER semantic_snapshot_inputs_validate
        BEFORE INSERT ON semantic_snapshot_inputs
        FOR EACH ROW EXECUTE FUNCTION validate_semantic_snapshot_input()
        """
    )


def _create_mysql_guards() -> None:
    op.execute(
        """
        CREATE TRIGGER semantic_snapshots_immutable_update
        BEFORE UPDATE ON semantic_snapshots FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SEMANTIC_SNAPSHOT_IMMUTABLE'
        """
    )
    op.execute(
        """
        CREATE TRIGGER semantic_snapshots_immutable_delete
        BEFORE DELETE ON semantic_snapshots FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SEMANTIC_SNAPSHOT_IMMUTABLE'
        """
    )
    op.execute(
        """
        CREATE TRIGGER semantic_snapshot_inputs_immutable_update
        BEFORE UPDATE ON semantic_snapshot_inputs FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SEMANTIC_SNAPSHOT_IMMUTABLE'
        """
    )
    op.execute(
        """
        CREATE TRIGGER semantic_snapshot_inputs_immutable_delete
        BEFORE DELETE ON semantic_snapshot_inputs FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SEMANTIC_SNAPSHOT_IMMUTABLE'
        """
    )
    op.execute(
        """
        CREATE TRIGGER semantic_snapshot_inputs_validate
        BEFORE INSERT ON semantic_snapshot_inputs FOR EACH ROW
        BEGIN
          IF (SELECT status FROM v2_pipeline_runs WHERE id = NEW.pipeline_run_id) <> 'success'
             OR (SELECT finished_at FROM v2_pipeline_runs WHERE id = NEW.pipeline_run_id) IS NULL
             OR (SELECT dataset_version_id FROM v2_pipeline_runs WHERE id = NEW.pipeline_run_id) IS NULL
             OR NOT EXISTS (
               SELECT 1 FROM pipeline_run_inputs
                WHERE pipeline_run_id = NEW.pipeline_run_id
                  AND dataset_version_id = NEW.dataset_version_id
             ) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SNAPSHOT_INPUT_NOT_GOVERNED';
          END IF;
        END
        """
    )


def _create_sqlite_guards() -> None:
    for table_name, action in (
        ("semantic_snapshots", "UPDATE"),
        ("semantic_snapshots", "DELETE"),
        ("semantic_snapshot_inputs", "UPDATE"),
        ("semantic_snapshot_inputs", "DELETE"),
    ):
        trigger_name = (
            f"{table_name}_immutable_{action.lower()}"
        )
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE {action} ON {table_name} "
            "BEGIN SELECT RAISE(ABORT, 'SEMANTIC_SNAPSHOT_IMMUTABLE'); END"
        )
    op.execute(
        """
        CREATE TRIGGER semantic_snapshot_inputs_validate
        BEFORE INSERT ON semantic_snapshot_inputs
        WHEN NOT EXISTS (
               SELECT 1 FROM v2_pipeline_runs AS run
                WHERE run.id = NEW.pipeline_run_id
                  AND run.status = 'success'
                  AND run.finished_at IS NOT NULL
                  AND run.dataset_version_id IS NOT NULL
             )
          OR NOT EXISTS (
               SELECT 1 FROM pipeline_run_inputs
                WHERE pipeline_run_id = NEW.pipeline_run_id
                  AND dataset_version_id = NEW.dataset_version_id
             )
        BEGIN
          SELECT RAISE(ABORT, 'SNAPSHOT_INPUT_NOT_GOVERNED');
        END
        """
    )


def _remediate_ungoverned_successful_runs() -> None:
    """Keep legacy success rows from violating the governed completion check."""
    op.execute(
        """
        UPDATE v2_pipeline_runs
           SET status = 'failed',
               error_log = CASE
                 WHEN error_log IS NULL OR error_log = ''
                 THEN 'MIGRATION_REMEDIATED_UNGOVERNED_SUCCESS'
                 ELSE error_log
               END
         WHERE status = 'success'
           AND (finished_at IS NULL OR dataset_version_id IS NULL)
        """
    )


def upgrade() -> None:
    # Existing releases were immutable before status existed. Temporarily
    # remove only that trigger while backfilling the new status column; the
    # manifest bytes and schema hash are never touched.
    _drop_release_immutability_guard()
    if _dialect_name() == "sqlite":
        # SQLite can add a constrained column without recreating the parent
        # table.  Keep this path in-place because mcp_write_requests and
        # other legacy tables may hold foreign keys to ontology_releases.
        op.execute(
            "ALTER TABLE ontology_releases ADD COLUMN status VARCHAR(20) "
            "NOT NULL DEFAULT 'published' "
            "CONSTRAINT ck_ontology_releases_status "
            "CHECK (status IN ('draft', 'published', 'revoked'))"
        )
    else:
        op.add_column("ontology_releases", sa.Column("status", sa.String(20), nullable=True))
    op.execute(
        """
        UPDATE ontology_releases AS release
           SET status = CASE
             WHEN EXISTS (
               SELECT 1 FROM ontology_projects AS project
                WHERE project.id = release.ontology_id
                  AND project.latest_published_release_id = release.id
             ) THEN 'published'
             WHEN EXISTS (
               SELECT 1 FROM ontology_projects AS project
                WHERE project.id = release.ontology_id
                  AND project.latest_published_release_id IS NOT NULL
             ) THEN 'revoked'
             ELSE 'draft'
           END
        """
    )
    if _dialect_name() != "sqlite":
        op.alter_column(
            "ontology_releases", "status", nullable=False,
            server_default=sa.text("'published'"), existing_type=sa.String(20),
        )
        op.create_check_constraint(
            "ck_ontology_releases_status", "ontology_releases",
            "status IN ('draft', 'published', 'revoked')",
        )
    _restore_release_immutability_guard()

    _remediate_ungoverned_successful_runs()
    if _dialect_name() == "sqlite":
        with op.batch_alter_table("v2_pipeline_runs", recreate="always") as batch_op:
            batch_op.create_check_constraint(
                "ck_v2_pipeline_runs_status",
                "status IN ('pending', 'running', 'success', 'failed', 'cancelled')",
            )
            batch_op.create_check_constraint(
                "ck_v2_pipeline_runs_success_completion",
                "status <> 'success' OR (finished_at IS NOT NULL AND dataset_version_id IS NOT NULL)",
            )
    else:
        op.create_check_constraint(
            "ck_v2_pipeline_runs_status", "v2_pipeline_runs",
            "status IN ('pending', 'running', 'success', 'failed', 'cancelled')",
        )
        op.create_check_constraint(
            "ck_v2_pipeline_runs_success_completion", "v2_pipeline_runs",
            "status <> 'success' OR (finished_at IS NOT NULL AND dataset_version_id IS NOT NULL)",
        )

    op.create_table(
        "semantic_snapshots",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("ontology_release_id", sa.String(36), nullable=False),
        sa.Column("quality_summary", sa.JSON(), nullable=False),
        sa.Column("evidence_summary", sa.JSON(), nullable=False),
        sa.Column("materialization_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="materialized"),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_snapshots"),
        sa.ForeignKeyConstraint(
            ["ontology_release_id"], ["ontology_releases.id"],
            name="fk_semantic_snapshots_ontology_release", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="fk_semantic_snapshots_creator", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("status IN ('materialized')", name="ck_semantic_snapshots_status"),
        sa.CheckConstraint("length(materialization_hash) = 64", name="ck_semantic_snapshots_materialization_hash"),
    )
    op.create_index("ix_semantic_snapshots_ontology_release_id", "semantic_snapshots", ["ontology_release_id"])
    if _dialect_name() == "postgresql":
        op.create_check_constraint(
            "ck_semantic_snapshots_materialization_hash_hex",
            "semantic_snapshots",
            "materialization_hash ~ '^[0-9a-f]{64}$'",
        )
    elif _dialect_name() == "mysql":
        op.create_check_constraint(
            "ck_semantic_snapshots_materialization_hash_hex",
            "semantic_snapshots",
            "materialization_hash REGEXP '^[0-9a-f]{64}$'",
        )

    op.create_table(
        "semantic_snapshot_inputs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("dataset_version_id", sa.String(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_snapshot_inputs"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["semantic_snapshots.id"],
            name="fk_semantic_snapshot_inputs_snapshot", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"], ["v2_dataset_versions.id"],
            name="fk_semantic_snapshot_inputs_dataset_version", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"], ["v2_pipeline_runs.id"],
            name="fk_semantic_snapshot_inputs_pipeline_run", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "snapshot_id", "dataset_version_id", "pipeline_run_id",
            name="uq_semantic_snapshot_inputs_identity",
        ),
    )
    op.create_index("ix_semantic_snapshot_inputs_snapshot_id", "semantic_snapshot_inputs", ["snapshot_id"])
    op.create_index("ix_semantic_snapshot_inputs_dataset_version_id", "semantic_snapshot_inputs", ["dataset_version_id"])
    op.create_index("ix_semantic_snapshot_inputs_pipeline_run_id", "semantic_snapshot_inputs", ["pipeline_run_id"])

    if _dialect_name() == "postgresql":
        _create_postgresql_guards()
    elif _dialect_name() == "mysql":
        _create_mysql_guards()
    elif _dialect_name() == "sqlite":
        _create_sqlite_guards()


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS semantic_snapshot_inputs_validate ON semantic_snapshot_inputs")
        op.execute("DROP FUNCTION IF EXISTS validate_semantic_snapshot_input()")
        op.execute("DROP TRIGGER IF EXISTS semantic_snapshot_inputs_immutable ON semantic_snapshot_inputs")
        op.execute("DROP TRIGGER IF EXISTS semantic_snapshots_immutable ON semantic_snapshots")
        op.execute("DROP FUNCTION IF EXISTS reject_semantic_snapshot_mutation()")
    elif dialect == "mysql":
        for trigger in (
            "semantic_snapshot_inputs_validate",
            "semantic_snapshot_inputs_immutable_delete",
            "semantic_snapshot_inputs_immutable_update",
            "semantic_snapshots_immutable_delete",
            "semantic_snapshots_immutable_update",
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    elif dialect == "sqlite":
        for trigger in (
            "semantic_snapshot_inputs_validate",
            "semantic_snapshot_inputs_immutable_delete",
            "semantic_snapshot_inputs_immutable_update",
            "semantic_snapshots_immutable_delete",
            "semantic_snapshots_immutable_update",
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger}")

    op.drop_index("ix_semantic_snapshot_inputs_pipeline_run_id", table_name="semantic_snapshot_inputs")
    op.drop_index("ix_semantic_snapshot_inputs_dataset_version_id", table_name="semantic_snapshot_inputs")
    op.drop_index("ix_semantic_snapshot_inputs_snapshot_id", table_name="semantic_snapshot_inputs")
    op.drop_table("semantic_snapshot_inputs")
    op.drop_index("ix_semantic_snapshots_ontology_release_id", table_name="semantic_snapshots")
    if dialect in {"postgresql", "mysql"}:
        op.drop_constraint(
            "ck_semantic_snapshots_materialization_hash_hex",
            "semantic_snapshots",
            type_="check",
        )
    op.drop_table("semantic_snapshots")
    if dialect == "sqlite":
        with op.batch_alter_table("v2_pipeline_runs", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_v2_pipeline_runs_success_completion", type_="check")
            batch_op.drop_constraint("ck_v2_pipeline_runs_status", type_="check")
        # DROP COLUMN is supported by SQLite 3.35+ and, unlike batch table
        # recreation, preserves existing FK references to ontology_releases.
        op.execute("ALTER TABLE ontology_releases DROP COLUMN status")
    else:
        op.drop_constraint("ck_v2_pipeline_runs_success_completion", "v2_pipeline_runs", type_="check")
        op.drop_constraint("ck_v2_pipeline_runs_status", "v2_pipeline_runs", type_="check")
        op.drop_constraint("ck_ontology_releases_status", "ontology_releases", type_="check")
        _drop_release_immutability_guard()
        op.drop_column("ontology_releases", "status")
    _restore_release_immutability_guard()
