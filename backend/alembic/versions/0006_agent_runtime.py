"""Agent runtime migration (P3A-INSTANCE: instance revision/relation sections).

Revision ID: 0006_agent_runtime
Revises: 0005_agent_configuration

P3A-INSTANCE owns the authoritative-instance sections: EntityInstance gains
`revision`, timezone `updated_at`, nullable `deleted_at` and a unique
`(ontology_id, entity_id, row_identity)` identity (created only when the
duplicate preflight is clean — duplicates are reported, never silently
deduped); `entity_id` FK changes CASCADE -> RESTRICT; Entity/Relation gain
soft-deprecation columns; `entity_instance_relations` is created with RESTRICT
FKs and a partial unique active-edge index.  Runtime/agent tables are added by
P3A-TURNDB in the same revision after this packet's handoff.
"""
import uuid

from alembic import op
import sqlalchemy as sa

from app.services.publication.cutover import (
    downgrade_instance_edge_guards,
    upgrade_instance_edge_guards,
)


revision = "0006_agent_runtime"
down_revision = "0005_agent_configuration"
branch_labels = None
depends_on = None

UUID = sa.String(36)
ENTITY_INSTANCE_FK = "entity_instances_entity_id_fkey"


def preflight_instance_duplicates(connection) -> list[dict]:
    """Duplicate (ontology_id, entity_id, row_identity) report.  Never aborts
    DDL; the unique identity index is created only when the report is empty."""
    rows = connection.execute(sa.text(
        "SELECT ontology_id, entity_id, row_identity, count(*) AS cnt "
        "FROM entity_instances "
        "GROUP BY ontology_id, entity_id, row_identity HAVING count(*) > 1 "
        "ORDER BY ontology_id, entity_id, row_identity"
    )).mappings().all()
    return [dict(row) for row in rows]


def upgrade_instance_revision_foundation() -> None:
    # EntityInstance: revision / updated_at / deleted_at + RESTRICT entity FK
    # (Entity/Relation soft-deprecation columns already exist since 0003).
    op.add_column("entity_instances", sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"))
    op.add_column("entity_instances", sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    ))
    op.add_column("entity_instances", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint(ENTITY_INSTANCE_FK, "entity_instances", type_="foreignkey")
    op.create_foreign_key(ENTITY_INSTANCE_FK, "entity_instances", "entities",
                          ["entity_id"], ["id"], ondelete="RESTRICT")

    # Unique identity index — only when the duplicate preflight is clean
    bind = op.get_bind()
    duplicates = preflight_instance_duplicates(bind)
    if duplicates:
        op.create_index("ix_entity_instances_identity", "entity_instances",
                        ["ontology_id", "entity_id", "row_identity"])
    else:
        op.create_index("uq_entity_instances_identity", "entity_instances",
                        ["ontology_id", "entity_id", "row_identity"], unique=True)

    # Authoritative instance relations (RESTRICT everywhere, active-edge unique)
    op.create_table(
        "entity_instance_relations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("ontology_id", UUID, nullable=False),
        sa.Column("source_instance_id", UUID, nullable=False),
        sa.Column("target_instance_id", UUID, nullable=False),
        sa.Column("relation_definition_id", UUID, nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_entity_instance_relations"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], name="fk_eir_ontology", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_instance_id"], ["entity_instances.id"], name="fk_eir_source", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_instance_id"], ["entity_instances.id"], name="fk_eir_target", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["relation_definition_id"], ["relations.id"], name="fk_eir_relation", ondelete="RESTRICT"),
    )
    op.create_index("ix_entity_instance_relations_ontology", "entity_instance_relations", ["ontology_id"])
    op.create_index("ix_entity_instance_relations_source", "entity_instance_relations", ["source_instance_id"])
    op.create_index("ix_entity_instance_relations_target", "entity_instance_relations", ["target_instance_id"])
    op.create_index("ix_entity_instance_relations_relation", "entity_instance_relations", ["relation_definition_id"])
    op.create_index(
        "uq_entity_instance_relations_active_edge", "entity_instance_relations",
        ["source_instance_id", "target_instance_id", "relation_definition_id"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade_instance_revision_foundation() -> None:
    op.drop_index("uq_entity_instance_relations_active_edge", table_name="entity_instance_relations")
    op.drop_table("entity_instance_relations")
    # Only one of the two identity indexes exists depending on the duplicate
    # preflight outcome; drop whichever is present.
    op.execute("DROP INDEX IF EXISTS uq_entity_instances_identity")
    op.execute("DROP INDEX IF EXISTS ix_entity_instances_identity")
    op.drop_constraint(ENTITY_INSTANCE_FK, "entity_instances", type_="foreignkey")
    op.create_foreign_key(ENTITY_INSTANCE_FK, "entity_instances", "entities",
                          ["entity_id"], ["id"], ondelete="CASCADE")
    op.drop_column("entity_instances", "deleted_at")
    op.drop_column("entity_instances", "updated_at")
    op.drop_column("entity_instances", "revision")


def upgrade_runtime_foundation() -> None:
    """Section 6 runtime graph without FK cycles: sessions -> turns -> messages
    with turn message links and the session active pointer added by ALTER, plus
    the transactional turn dispatch outbox."""
    op.create_table(
        "agent_sessions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("agent_id", UUID, nullable=False),
        sa.Column("owner_user_id", UUID, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("active_turn_id", UUID, nullable=True),  # FK added later (SET NULL)
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_sessions"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_agent_sessions_agent", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_agent_sessions_owner", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('active', 'closed')", name="ck_agent_sessions_status"),
    )
    op.create_index("ix_agent_sessions_agent", "agent_sessions", ["agent_id"])
    op.create_table(
        "agent_turns",
        sa.Column("id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("request_message_id", UUID, nullable=True),  # FKs added later (RESTRICT)
        sa.Column("response_message_id", UUID, nullable=True),
        sa.Column("dispatch_generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("claim_generation", sa.BigInteger(), nullable=True),
        sa.Column("claim_token", sa.String(128), nullable=True),
        sa.Column("worker_artifact_id", UUID, nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_turns"),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], name="fk_agent_turns_session", ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_approval', 'awaiting_clarification', "
            "'reconciling', 'cancelling', 'succeeded', 'failed', 'cancelled')",
            name="ck_agent_turns_status",
        ),
    )
    op.create_index("ix_agent_turns_session", "agent_turns", ["session_id"])
    op.create_table(
        "agent_messages",
        sa.Column("id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_messages"),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], name="fk_agent_messages_session", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_agent_messages_turn", ondelete="RESTRICT"),
        sa.UniqueConstraint("session_id", "ordinal", name="uq_agent_messages_session_ordinal"),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name="ck_agent_messages_role"),
    )
    # Step 4: turn request/response message FKs (RESTRICT)
    op.create_foreign_key("fk_agent_turns_request_message", "agent_turns", "agent_messages",
                          ["request_message_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_agent_turns_response_message", "agent_turns", "agent_messages",
                          ["response_message_id"], ["id"], ondelete="RESTRICT")
    # Step 5: session active-turn pointer FK (SET NULL)
    op.create_foreign_key("fk_agent_sessions_active_turn", "agent_sessions", "agent_turns",
                          ["active_turn_id"], ["id"], ondelete="SET NULL")
    # One active Turn per session (partial unique index)
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_turns_active_session ON agent_turns (session_id) "
        "WHERE status IN ('queued', 'running', 'awaiting_approval', "
        "'awaiting_clarification', 'reconciling', 'cancelling')"
    )
    op.create_table(
        "agent_turn_dispatch_outbox",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("dispatch_generation", sa.BigInteger(), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("broker_message_id", sa.String(128), nullable=True),
        sa.Column("claim_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_outbox_id", UUID, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_turn_dispatch_outbox"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_atdo_turn", ondelete="RESTRICT"),
        sa.UniqueConstraint("turn_id", "dispatch_generation", "operation", name="uq_agent_turn_dispatch_outbox_identity"),
    )
    op.create_index("ix_agent_turn_dispatch_outbox_turn", "agent_turn_dispatch_outbox", ["turn_id"])


def downgrade_runtime_foundation() -> None:
    op.drop_index("ix_agent_turn_dispatch_outbox_turn", table_name="agent_turn_dispatch_outbox")
    op.drop_table("agent_turn_dispatch_outbox")
    op.execute("DROP INDEX IF EXISTS uq_agent_turns_active_session")
    op.drop_constraint("fk_agent_sessions_active_turn", "agent_sessions", type_="foreignkey")
    op.drop_constraint("fk_agent_turns_response_message", "agent_turns", type_="foreignkey")
    op.drop_constraint("fk_agent_turns_request_message", "agent_turns", type_="foreignkey")
    op.drop_table("agent_messages")
    op.drop_index("ix_agent_turns_session", table_name="agent_turns")
    op.drop_table("agent_turns")
    op.drop_index("ix_agent_sessions_agent", table_name="agent_sessions")
    op.drop_table("agent_sessions")


def upgrade_derived_index_outbox() -> None:
    """Transactional derived-index outbox + trigger on authoritative
    instance/relation rows.  Router, mapping and import writers all mutate
    these tables directly, so a BEFORE trigger guarantees exactly one
    release-aware upsert/delete event per mutation regardless of writer."""
    op.create_table(
        "agent_index_outbox",
        sa.Column("id", UUID, nullable=False),
        sa.Column("ontology_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("instance_id", UUID, nullable=True),
        sa.Column("instance_revision", sa.BigInteger(), nullable=True),
        sa.Column("entity_id", UUID, nullable=True),
        sa.Column("edge_id", UUID, nullable=True),
        sa.Column("source_instance_id", UUID, nullable=True),
        sa.Column("target_instance_id", UUID, nullable=True),
        sa.Column("relation_definition_id", UUID, nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_index_outbox"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], name="fk_aio_ontology", ondelete="RESTRICT"),
        sa.CheckConstraint(
            "event_type IN ('upsert_instance', 'delete_instance', 'upsert_edge', 'delete_edge')",
            name="ck_agent_index_outbox_event_type",
        ),
    )
    op.create_index("ix_agent_index_outbox_ontology", "agent_index_outbox", ["ontology_id"])
    op.execute(
        """
        CREATE FUNCTION emit_agent_index_outbox() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          evt text;
          payload json;
        BEGIN
          IF TG_TABLE_NAME = 'entity_instances' THEN
            IF TG_OP = 'DELETE' THEN
              evt := 'delete_instance';
              payload := json_build_object('row_identity', OLD.row_identity, 'deleted_at', OLD.deleted_at);
              EXECUTE format(
                'INSERT INTO %I.agent_index_outbox (id, ontology_id, event_type, instance_id, instance_revision, entity_id, payload) '
                'VALUES (gen_random_uuid(), %L, %L, %L, %L, %L, %L::json)',
                TG_TABLE_SCHEMA, OLD.ontology_id, evt, OLD.id, OLD.revision, OLD.entity_id, payload::text);
            ELSE
              IF NEW.deleted_at IS NOT NULL THEN evt := 'delete_instance';
              ELSE evt := 'upsert_instance'; END IF;
              payload := json_build_object('row_identity', NEW.row_identity, 'deleted_at', NEW.deleted_at);
              EXECUTE format(
                'INSERT INTO %I.agent_index_outbox (id, ontology_id, event_type, instance_id, instance_revision, entity_id, payload) '
                'VALUES (gen_random_uuid(), %L, %L, %L, %L, %L, %L::json)',
                TG_TABLE_SCHEMA, NEW.ontology_id, evt, NEW.id, NEW.revision, NEW.entity_id, payload::text);
            END IF;
          ELSE
            IF TG_OP = 'DELETE' THEN
              evt := 'delete_edge';
              payload := json_build_object('deleted_at', OLD.deleted_at);
              EXECUTE format(
                'INSERT INTO %I.agent_index_outbox (id, ontology_id, event_type, edge_id, source_instance_id, '
                'target_instance_id, relation_definition_id, payload) '
                'VALUES (gen_random_uuid(), %L, %L, %L, %L, %L, %L, %L::json)',
                TG_TABLE_SCHEMA, OLD.ontology_id, evt, OLD.id, OLD.source_instance_id,
                OLD.target_instance_id, OLD.relation_definition_id, payload::text);
            ELSE
              IF NEW.deleted_at IS NOT NULL THEN evt := 'delete_edge';
              ELSE evt := 'upsert_edge'; END IF;
              payload := json_build_object('deleted_at', NEW.deleted_at);
              EXECUTE format(
                'INSERT INTO %I.agent_index_outbox (id, ontology_id, event_type, edge_id, source_instance_id, '
                'target_instance_id, relation_definition_id, payload) '
                'VALUES (gen_random_uuid(), %L, %L, %L, %L, %L, %L, %L::json)',
                TG_TABLE_SCHEMA, NEW.ontology_id, evt, NEW.id, NEW.source_instance_id,
                NEW.target_instance_id, NEW.relation_definition_id, payload::text);
            END IF;
          END IF;
          RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER agent_index_outbox_instances AFTER INSERT OR UPDATE OR DELETE "
        "ON entity_instances FOR EACH ROW EXECUTE FUNCTION emit_agent_index_outbox()"
    )
    op.execute(
        "CREATE TRIGGER agent_index_outbox_relations AFTER INSERT OR UPDATE OR DELETE "
        "ON entity_instance_relations FOR EACH ROW EXECUTE FUNCTION emit_agent_index_outbox()"
    )


def downgrade_derived_index_outbox() -> None:
    op.execute("DROP TRIGGER IF EXISTS agent_index_outbox_relations ON entity_instance_relations")
    op.execute("DROP TRIGGER IF EXISTS agent_index_outbox_instances ON entity_instances")
    op.execute("DROP FUNCTION IF EXISTS emit_agent_index_outbox()")
    op.drop_index("ix_agent_index_outbox_ontology", table_name="agent_index_outbox")
    op.drop_table("agent_index_outbox")


def upgrade_runtime_artifact_schema() -> None:
    """Remaining Section 6 step-6 tables: runtime artifact registry, checkpoints,
    node/model executions, trace events, approvals, reconciliation cases,
    clarification requests, application-state snapshots, tool executions,
    stream tickets and fixed-minimum purge job/marker.  All are exclusive to
    0006 (P3A-TURNDB owns the file; no later packet may edit it)."""
    op.create_table(
        "runtime_artifacts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("runtime_kind", sa.String(40), nullable=False),
        sa.Column("runtime_library_version", sa.String(100), nullable=False),
        sa.Column("graph_revision", sa.String(64), nullable=False),
        sa.Column("serializer_version", sa.String(20), nullable=False),
        sa.Column("image_digest", sa.String(128), nullable=False),
        sa.Column("queue_name", sa.String(120), nullable=False),
        sa.Column("checkpoint_formats", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_runtime_artifacts"),
        sa.UniqueConstraint("runtime_kind", "runtime_library_version", "graph_revision",
                            "serializer_version", name="uq_runtime_artifacts_tuple"),
        sa.UniqueConstraint("queue_name", name="uq_runtime_artifacts_queue"),
    )
    op.create_table(
        "agent_turn_checkpoints",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("runtime_artifact_id", UUID, nullable=True),
        sa.Column("checkpoint_namespace", sa.String(120), nullable=False),
        sa.Column("checkpoint_id", sa.String(120), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(120), nullable=True),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("next_nodes", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("serialized_state", sa.LargeBinary(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("last_event_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("model_tuple", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("retrieval_tuple", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("application_state_tuple", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("summary_range", sa.String(64), nullable=True),
        sa.Column("summary_hash", sa.String(64), nullable=True),
        sa.Column("pending_execution_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_turn_checkpoints"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_atc_turn", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["runtime_artifact_id"], ["runtime_artifacts.id"], name="fk_atc_artifact", ondelete="RESTRICT"),
        sa.UniqueConstraint("turn_id", "checkpoint_namespace", "checkpoint_id", name="uq_atc_namespace_id"),
        sa.UniqueConstraint("turn_id", "revision", name="uq_atc_turn_revision"),
    )
    op.create_table(
        "agent_turn_checkpoint_writes",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("checkpoint_namespace", sa.String(120), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(120), nullable=False),
        sa.Column("task_id", sa.String(120), nullable=False),
        sa.Column("task_path", sa.String(120), nullable=False),
        sa.Column("channel", sa.String(120), nullable=False),
        sa.Column("write_index", sa.Integer(), nullable=False),
        sa.Column("serializer_version", sa.String(20), nullable=False),
        sa.Column("value_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("value_hash", sa.String(64), nullable=False),
        sa.Column("consumed_child_checkpoint_id", sa.String(120), nullable=True),
        sa.Column("attempt_state", sa.String(30), nullable=False, server_default="started"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_turn_checkpoint_writes"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_atcw_turn", ondelete="RESTRICT"),
        sa.UniqueConstraint("turn_id", "checkpoint_namespace", "parent_checkpoint_id", "task_id",
                            "task_path", "channel", "write_index", name="uq_atcw_identity"),
    )
    op.create_table(
        "agent_node_executions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(120), nullable=False),
        sa.Column("task_id", sa.String(120), nullable=False),
        sa.Column("task_path", sa.String(120), nullable=False),
        sa.Column("node_name", sa.String(120), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False, server_default="started"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_node_executions"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_ane_turn", ondelete="RESTRICT"),
        sa.UniqueConstraint("turn_id", "parent_checkpoint_id", "task_id", "task_path",
                            "node_name", "attempt_no", name="uq_ane_attempt"),
    )
    op.create_table(
        "agent_model_invocations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("node_execution_id", UUID, nullable=True),
        sa.Column("invocation_slot", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_model_invocations"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_ami_turn", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["node_execution_id"], ["agent_node_executions.id"], name="fk_ami_node", ondelete="RESTRICT"),
        sa.UniqueConstraint("turn_id", "node_execution_id", "invocation_slot", name="uq_ami_slot"),
        sa.UniqueConstraint("idempotency_key", name="uq_ami_idempotency"),
    )
    op.create_table(
        "agent_runtime_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runtime_events"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_are_turn", ondelete="RESTRICT"),
        sa.UniqueConstraint("turn_id", "sequence", name="uq_are_turn_sequence"),
    )
    op.create_table(
        "agent_tool_executions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("node_execution_id", UUID, nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("descriptor", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("parameters_hash", sa.String(64), nullable=False),
        sa.Column("preview_hash", sa.String(64), nullable=True),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_tool_executions"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_ate_turn", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["node_execution_id"], ["agent_node_executions.id"], name="fk_ate_node", ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_tool_executions_idempotency"),
    )
    op.create_table(
        "agent_approvals",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("tool_execution_id", UUID, nullable=False),
        sa.Column("node_execution_id", UUID, nullable=True),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("parameter_hash", sa.String(64), nullable=False),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("release_hash", sa.String(64), nullable=False),
        sa.Column("model_hash", sa.String(64), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("designated_actor_id", UUID, nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("stale_reason", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_approvals"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_aa_turn", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tool_execution_id"], ["agent_tool_executions.id"], name="fk_aa_execution", ondelete="RESTRICT"),
        sa.UniqueConstraint("tool_execution_id", name="uq_agent_approvals_execution"),
    )
    op.create_table(
        "agent_reconciliation_cases",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("execution_kind", sa.String(40), nullable=False),
        sa.Column("execution_id", UUID, nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(30), nullable=False, server_default="open"),
        sa.Column("provider_request_id", sa.String(128), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("evidence_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("evidence_hash", sa.String(64), nullable=True),
        sa.Column("investigator_id", UUID, nullable=True),
        sa.Column("resolver_id", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_reconciliation_cases"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_arc_turn", ondelete="RESTRICT"),
        sa.UniqueConstraint("turn_id", "execution_kind", "execution_id", name="uq_arc_identity"),
    )
    op.create_table(
        "agent_clarification_requests",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("base_request_revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_agent_clarification_requests"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_acr_turn", ondelete="RESTRICT"),
    )
    op.create_table(
        "agent_application_state_snapshots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("parent_revision", sa.BigInteger(), nullable=True),
        sa.Column("schema_version_id", UUID, nullable=False),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("canonical_hash", sa.String(64), nullable=False),
        sa.Column("actor_user_id", UUID, nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("selected_instances", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_application_state_snapshots"),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], name="fk_aass_session", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["schema_version_id"], ["application_state_schema_versions.id"], name="fk_aass_schema", ondelete="RESTRICT"),
        sa.UniqueConstraint("session_id", "revision", name="uq_aass_session_revision"),
    )
    op.create_table(
        "agent_stream_tickets",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("actor_user_id", UUID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="unused"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_stream_tickets"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_ast_turn", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_ast_actor", ondelete="RESTRICT"),
    )
    op.create_table(
        "agent_purge_jobs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("security_domain_id", UUID, nullable=False),
        sa.Column("purge_class", sa.String(40), nullable=False),
        sa.Column("cursor_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor_id", UUID, nullable=True),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("claim_token", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_purge_jobs"),
        sa.ForeignKeyConstraint(["security_domain_id"], ["security_domains.id"], name="fk_apj_domain", ondelete="RESTRICT"),
        sa.UniqueConstraint("security_domain_id", "purge_class", name="uq_agent_purge_jobs_domain_class"),
    )
    op.create_table(
        "agent_purge_markers",
        sa.Column("id", UUID, nullable=False),
        sa.Column("turn_id", UUID, nullable=False),
        sa.Column("policy_version_id", UUID, nullable=True),
        sa.Column("fixed_policy_hash", sa.String(64), nullable=False),
        sa.Column("job_id", UUID, nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_purge_markers"),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"], name="fk_apm_turn", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["agent_purge_jobs.id"], name="fk_apm_job", ondelete="RESTRICT"),
    )


def downgrade_runtime_artifact_schema() -> None:
    for table in ("agent_purge_markers", "agent_purge_jobs", "agent_stream_tickets",
                  "agent_application_state_snapshots", "agent_clarification_requests",
                  "agent_reconciliation_cases", "agent_approvals", "agent_tool_executions",
                  "agent_runtime_events", "agent_model_invocations",
                  "agent_node_executions", "agent_turn_checkpoint_writes",
                  "agent_turn_checkpoints", "runtime_artifacts"):
        op.drop_table(table)


def upgrade() -> None:
    upgrade_instance_revision_foundation()
    upgrade_instance_edge_guards()
    upgrade_runtime_foundation()
    upgrade_runtime_artifact_schema()
    upgrade_derived_index_outbox()


def downgrade() -> None:
    downgrade_derived_index_outbox()
    downgrade_runtime_artifact_schema()
    downgrade_runtime_foundation()
    downgrade_instance_edge_guards()
    downgrade_instance_revision_foundation()
