"""Task 6: durable source refresh contract — refresh_source_states,
refresh_runs (+ append-only transitions), refresh_inbox_events,
refresh_outbox_events, refresh_dead_letters, refresh_schedules, and the
pipeline_run_inputs multi-source lineage table. Single Phase 2 refresh-state
migration — later migrations must not recreate or shadow these tables.

Revision ID: 0022_refresh_contract
Revises: 0021_mapping_entity_class_cn
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_refresh_contract"
down_revision = "0021_mapping_entity_class_cn"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # refresh_runs is created first: refresh_source_states,
    # refresh_run_transitions, refresh_dead_letters and refresh_schedules
    # all reference refresh_runs.id, while refresh_runs itself only ever
    # references a source/resource by string (no FK back to
    # refresh_source_states — the authoritative row is looked up by the
    # (source_id, resource) unique key instead).
    op.create_table(
        "refresh_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("resource", sa.String(200), nullable=False),
        sa.Column("policy", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=True),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("cursor_contract", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("dispatch_state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("dispatch_reason", sa.Text(), nullable=True),
        sa.Column("cursor_before", sa.JSON(), nullable=True),
        sa.Column("cursor_after", sa.JSON(), nullable=True),
        # Deliberately not a foreign key — see PipelineRunInput's docstring
        # in app/models/v2/pipeline.py for why lineage pointers here are
        # soft references rather than FK-enforced. Not a raw UUID either:
        # a governed connector/orchestrator may hand back any opaque id.
        sa.Column("pipeline_run_id", sa.String(200), nullable=True),
        sa.Column("source_provenance", sa.JSON(), nullable=True),
        sa.Column("quality_summary", sa.JSON(), nullable=True),
        sa.Column("lag_seconds", sa.Integer(), nullable=True),
        sa.Column("duplicate_count", sa.Integer(), nullable=True),
        sa.Column("late_count", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("lease_owner", sa.String(200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_by", sa.String(200), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("cancel_fencing_token", sa.Integer(), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "source_id", "resource", "config_version", "idempotency_key",
            name="uq_refresh_runs_idempotency_scope",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','cancel_requested','cancelled','succeeded','failed','dead_lettered')",
            name="ck_refresh_runs_status",
        ),
        sa.CheckConstraint(
            "dispatch_state IN ('pending','dispatched','backpressured','publish_failed')",
            name="ck_refresh_runs_dispatch_state",
        ),
    )
    op.create_index("ix_refresh_runs_source_resource", "refresh_runs", ["source_id", "resource"])
    op.create_index("ix_refresh_runs_status", "refresh_runs", ["status"])
    op.create_index("ix_refresh_runs_lease_expires_at", "refresh_runs", ["lease_expires_at"])
    op.create_index("ix_refresh_runs_cancel_requested_at", "refresh_runs", ["cancel_requested_at"])

    op.create_table(
        "refresh_source_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("resource", sa.String(200), nullable=False),
        sa.Column("cursor_contract", sa.String(30), nullable=False),
        sa.Column("cursor", sa.JSON(), nullable=True),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("configuration", sa.JSON(), nullable=True),
        sa.Column("lease_owner", sa.String(200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_successful_run_id", sa.String(36),
                  sa.ForeignKey("refresh_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("source_id", "resource", name="uq_refresh_source_states_source_resource"),
    )
    op.create_index("ix_refresh_source_states_lease_expires_at", "refresh_source_states", ["lease_expires_at"])

    op.create_table(
        "refresh_run_transitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("refresh_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_refresh_run_transitions_run_id", "refresh_run_transitions", ["run_id"])

    op.create_table(
        "refresh_inbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("resource", sa.String(200), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="received"),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("source_id", "resource", "event_id", name="uq_refresh_inbox_events_identity"),
        sa.CheckConstraint(
            "state IN ('received','duplicate','processed','dead_lettered')",
            name="ck_refresh_inbox_events_state",
        ),
    )

    op.create_table(
        "refresh_outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("resource", sa.String(200), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("state IN ('pending','published','failed')", name="ck_refresh_outbox_events_state"),
    )
    op.create_index("ix_refresh_outbox_events_state", "refresh_outbox_events", ["state"])

    op.create_table(
        "refresh_dead_letters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("resource", sa.String(200), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("refresh_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("replay_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "replay_status IN ('pending','replayed','discarded')", name="ck_refresh_dead_letters_replay_status",
        ),
    )

    op.create_table(
        "refresh_schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(200), nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("business_calendar_id", sa.String(100), nullable=True),
        sa.Column("excluded_dates", sa.JSON(), nullable=True),
        sa.Column("sla_seconds", sa.Integer(), nullable=True),
        sa.Column("retry_policy", sa.JSON(), nullable=True),
        sa.Column("backfill_window_seconds", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_dispatched_run_id", sa.String(36),
                  sa.ForeignKey("refresh_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("target_type", "target_id", name="uq_refresh_schedules_target"),
        sa.CheckConstraint("target_type IN ('source','pipeline')", name="ck_refresh_schedules_target_type"),
    )
    op.create_index("ix_refresh_schedules_next_due_at", "refresh_schedules", ["next_due_at"])

    op.create_table(
        "pipeline_run_inputs",
        sa.Column("id", sa.String(36), primary_key=True),
        # Deliberately not foreign keys — see PipelineRunInput's docstring in
        # app/models/v2/pipeline.py.
        sa.Column("pipeline_run_id", sa.String(200), nullable=False),
        sa.Column("dataset_version_id", sa.String(200), nullable=False),
        sa.Column("source_cursor", sa.JSON(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("input_ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("pipeline_run_id", "input_ordinal", name="uq_pipeline_run_inputs_ordinal"),
    )
    op.create_index("ix_pipeline_run_inputs_pipeline_run_id", "pipeline_run_inputs", ["pipeline_run_id"])
    op.create_index("ix_pipeline_run_inputs_dataset_version_id", "pipeline_run_inputs", ["dataset_version_id"])

    # Default connection-level / resource-level (Dataset) refresh mode and
    # cursor contract (Task 6 Interfaces: "a connection/resource may select
    # one mode and one cursor contract"). Nullable — validity is enforced by
    # app.services.v2.incremental.contract at write time, not by a DB
    # constraint, matching how ConnectionKind/status are stored elsewhere in
    # this file's existing tables.
    op.add_column("v2_connections", sa.Column("refresh_policy", sa.String(20), nullable=True))
    op.add_column("v2_connections", sa.Column("cursor_contract", sa.String(30), nullable=True))
    op.add_column("v2_datasets", sa.Column("refresh_policy", sa.String(20), nullable=True))
    op.add_column("v2_datasets", sa.Column("cursor_contract", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("v2_datasets", "cursor_contract")
    op.drop_column("v2_datasets", "refresh_policy")
    op.drop_column("v2_connections", "cursor_contract")
    op.drop_column("v2_connections", "refresh_policy")

    op.drop_index("ix_pipeline_run_inputs_dataset_version_id", table_name="pipeline_run_inputs")
    op.drop_index("ix_pipeline_run_inputs_pipeline_run_id", table_name="pipeline_run_inputs")
    op.drop_table("pipeline_run_inputs")

    op.drop_index("ix_refresh_schedules_next_due_at", table_name="refresh_schedules")
    op.drop_table("refresh_schedules")

    op.drop_table("refresh_dead_letters")

    op.drop_index("ix_refresh_outbox_events_state", table_name="refresh_outbox_events")
    op.drop_table("refresh_outbox_events")

    op.drop_table("refresh_inbox_events")

    op.drop_index("ix_refresh_run_transitions_run_id", table_name="refresh_run_transitions")
    op.drop_table("refresh_run_transitions")

    op.drop_index("ix_refresh_source_states_lease_expires_at", table_name="refresh_source_states")
    op.drop_table("refresh_source_states")

    op.drop_index("ix_refresh_runs_cancel_requested_at", table_name="refresh_runs")
    op.drop_index("ix_refresh_runs_lease_expires_at", table_name="refresh_runs")
    op.drop_index("ix_refresh_runs_status", table_name="refresh_runs")
    op.drop_index("ix_refresh_runs_source_resource", table_name="refresh_runs")
    op.drop_table("refresh_runs")
