"""Agent configuration schema (P2B-SCHEMA).

Revision ID: 0005_agent_configuration
Revises: 0004_roles_model_versions

Additive Agent/version/grant/policy/retrieval-source/application-state-schema/
provider/connection/prompt-provenance tables.  No Agent is backfilled; the
built-in `chat-v1` application-state schema is seeded idempotently (canonical
immutable version 1 allowing only `locale`, exact hash, active pointer) and
never runs at startup.
"""
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "0005_agent_configuration"
down_revision = "0004_roles_model_versions"
branch_labels = None
depends_on = None

UUID = sa.String(36)


def _new_id() -> str:
    return str(uuid.uuid4())


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def upgrade_agent_configuration_foundation() -> None:
    op.create_table(
        "tool_providers",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_tool_providers"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_tool_providers_creator"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_tool_providers_status"),
    )
    op.create_table(
        "tool_connections",
        sa.Column("id", UUID, nullable=False),
        sa.Column("provider_id", UUID, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("active_version_id", UUID, nullable=True),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_tool_connections"),
        sa.ForeignKeyConstraint(["provider_id"], ["tool_providers.id"], name="fk_tool_connections_provider", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_tool_connections_creator"),
        sa.CheckConstraint("status IN ('active', 'disabled', 'revoked')", name="ck_tool_connections_status"),
    )
    op.create_index("ix_tool_connections_provider", "tool_connections", ["provider_id"])
    op.create_table(
        "tool_connection_versions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("connection_id", UUID, nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("endpoint", sa.String(500), nullable=True),
        sa.Column("audience", sa.String(200), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("credential_reference", sa.String(200), nullable=True),
        sa.Column("allowlists", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("approval_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("health_status", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_tool_connection_versions"),
        sa.ForeignKeyConstraint(["connection_id"], ["tool_connections.id"], name="fk_tcv_connection", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_tcv_creator"),
        sa.UniqueConstraint("connection_id", "version_no", name="uq_tcv_connection_version"),
        sa.CheckConstraint("approval_status IN ('approved', 'pending', 'rejected')", name="ck_tcv_approval"),
        sa.CheckConstraint("health_status IN ('healthy', 'unhealthy', 'unknown')", name="ck_tcv_health"),
    )
    op.create_index("ix_tool_connection_versions_connection", "tool_connection_versions", ["connection_id"])
    op.create_foreign_key(
        "fk_tool_connections_active_version", "tool_connections", "tool_connection_versions",
        ["active_version_id"], ["id"], ondelete="RESTRICT",
    )

    op.create_table(
        "application_state_schema_registries",
        sa.Column("id", UUID, nullable=False),
        sa.Column("application_key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("active_version_id", UUID, nullable=True),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_assr"),
        sa.UniqueConstraint("application_key", name="uq_assr_application_key"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_assr_status"),
    )
    op.create_table(
        "application_state_schema_versions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("registry_id", UUID, nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("json_schema", sa.JSON(), nullable=False),
        sa.Column("canonical_hash", sa.String(64), nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_assv"),
        sa.ForeignKeyConstraint(["registry_id"], ["application_state_schema_registries.id"], name="fk_assv_registry", ondelete="RESTRICT"),
        sa.UniqueConstraint("registry_id", "version_no", name="uq_assv_registry_version"),
    )
    op.create_index("ix_assv_registry", "application_state_schema_versions", ["registry_id"])
    op.create_foreign_key(
        "fk_assr_active_version", "application_state_schema_registries", "application_state_schema_versions",
        ["active_version_id"], ["id"], ondelete="RESTRICT",
    )

    op.create_table(
        "agents",
        sa.Column("id", UUID, nullable=False),
        sa.Column("visibility", sa.String(12), nullable=False, server_default="private"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("active_version_id", UUID, nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agents"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_agents_owner"),
        sa.CheckConstraint("visibility IN ('private', 'restricted')", name="ck_agents_visibility"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_agents_status"),
    )
    op.create_index("ix_agents_owner", "agents", ["owner_id"])
    op.create_table(
        "prompt_generations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("agent_id", UUID, nullable=False),
        sa.Column("base_version_no", sa.BigInteger(), nullable=False),
        sa.Column("model_config_version_id", UUID, nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requester_id", UUID, nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_prompt_generations"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_pg_agent", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_config_version_id"], ["model_config_versions.id"], name="fk_pg_model_version", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"], name="fk_pg_requester"),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="ck_pg_status"),
    )
    op.create_index("ix_prompt_generations_agent", "prompt_generations", ["agent_id"])
    op.create_table(
        "agent_versions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("agent_id", UUID, nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_model_config_version_id", UUID, nullable=False),
        sa.Column("default_model_name", sa.String(200), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("memory_settings", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("application_state_schema_version_id", UUID, nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("prompt_generation_id", UUID, nullable=True),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_versions"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_agent_versions_agent", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["default_model_config_version_id"], ["model_config_versions.id"], name="fk_agent_versions_model", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["application_state_schema_version_id"], ["application_state_schema_versions.id"], name="fk_agent_versions_appschema", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["prompt_generation_id"], ["prompt_generations.id"], name="fk_agent_versions_pg", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_agent_versions_creator"),
        sa.UniqueConstraint("agent_id", "version_no", name="uq_agent_versions_agent_version"),
    )
    op.create_index("ix_agent_versions_agent", "agent_versions", ["agent_id"])
    op.create_foreign_key(
        "fk_agents_active_version", "agents", "agent_versions",
        ["active_version_id"], ["id"], ondelete="RESTRICT",
    )

    op.create_table(
        "agent_access_grants",
        sa.Column("id", UUID, nullable=False),
        sa.Column("agent_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID, nullable=False),
        sa.Column("revoked_by", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_access_grants"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_aag_agent", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_aag_user", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_aag_creator"),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"], name="fk_aag_revoker"),
        sa.CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_agent_access_grants_status"),
    )
    op.create_index("ix_agent_access_grants_agent", "agent_access_grants", ["agent_id"])
    op.create_index("ix_agent_access_grants_user", "agent_access_grants", ["user_id"])

    op.create_table(
        "agent_ontology_bindings",
        sa.Column("id", UUID, nullable=False),
        sa.Column("agent_version_id", UUID, nullable=False),
        sa.Column("ontology_id", UUID, nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("allowlists", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_ontology_bindings"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], name="fk_aob_version", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], name="fk_aob_ontology", ondelete="RESTRICT"),
    )
    op.create_index("ix_agent_ontology_bindings_version", "agent_ontology_bindings", ["agent_version_id"])
    op.create_index("ix_agent_ontology_bindings_ontology", "agent_ontology_bindings", ["ontology_id"])

    op.create_table(
        "agent_external_tool_bindings",
        sa.Column("id", UUID, nullable=False),
        sa.Column("agent_version_id", UUID, nullable=False),
        sa.Column("tool_connection_version_id", UUID, nullable=False),
        sa.Column("alias", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_external_tool_bindings"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], name="fk_aetb_version", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tool_connection_version_id"], ["tool_connection_versions.id"], name="fk_aetb_connection_version", ondelete="RESTRICT"),
    )
    op.create_index("ix_agent_external_tool_bindings_version", "agent_external_tool_bindings", ["agent_version_id"])

    op.create_table(
        "agent_retrieval_sources",
        sa.Column("id", UUID, nullable=False),
        sa.Column("agent_version_id", UUID, nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("applicability_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_retrieval_sources"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], name="fk_ars_version", ondelete="RESTRICT"),
        sa.UniqueConstraint("agent_version_id", "source_id", name="uq_ars_version_source"),
        sa.CheckConstraint("kind IN ('fixed', 'semantic', 'document', 'function')", name="ck_ars_kind"),
    )
    op.create_index("ix_agent_retrieval_sources_version", "agent_retrieval_sources", ["agent_version_id"])


def seed_application_state_schemas() -> None:
    """Idempotently seed the built-in `chat-v1` application-state schema.

    Canonical immutable version 1 allows only `locale`; a same-key/different-
    hash collision fails loudly.  Never runs at startup (migration only)."""
    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT active_version_id FROM application_state_schema_registries "
                "WHERE application_key = 'chat-v1'")
    ).mappings().one_or_none()
    if existing is not None:
        if existing["active_version_id"]:
            return
        raise RuntimeError("CHAT_V1_SEED_AMBIGUOUS")
    json_schema = {
        "type": "object",
        "properties": {"locale": {"type": "string"}},
        "additionalProperties": False,
    }
    canonical_hash = hashlib.sha256(_canonical_json(json_schema).encode()).hexdigest()
    registry_id = _new_id()
    version_id = _new_id()
    op.execute(
        sa.text(
            "INSERT INTO application_state_schema_registries "
            "(id, application_key, status, active_version_id, created_by, created_at, updated_at) "
            "VALUES (:id, 'chat-v1', 'active', NULL, NULL, now(), now())"
        ).bindparams(id=registry_id)
    )
    op.execute(
        sa.text(
            "INSERT INTO application_state_schema_versions "
            "(id, registry_id, version_no, json_schema, canonical_hash, created_by, created_at) "
            "VALUES (:id, :registry, 1, CAST(:schema AS json), :hash, NULL, now())"
        ).bindparams(id=version_id, registry=registry_id, schema=_canonical_json(json_schema), hash=canonical_hash)
    )
    op.execute(
        sa.text(
            "UPDATE application_state_schema_registries SET active_version_id = :vid "
            "WHERE id = :rid"
        ).bindparams(vid=version_id, rid=registry_id)
    )


def upgrade() -> None:
    upgrade_agent_configuration_foundation()
    seed_application_state_schemas()


def downgrade_agent_configuration_foundation() -> None:
    op.drop_table("agent_retrieval_sources")
    op.drop_table("agent_external_tool_bindings")
    op.drop_table("agent_ontology_bindings")
    op.drop_table("agent_access_grants")
    op.drop_constraint("fk_agents_active_version", "agents", type_="foreignkey")
    op.drop_table("agent_versions")
    op.drop_table("prompt_generations")
    op.drop_table("agents")
    op.drop_constraint("fk_assr_active_version", "application_state_schema_registries", type_="foreignkey")
    op.drop_table("application_state_schema_versions")
    op.drop_table("application_state_schema_registries")
    op.drop_constraint("fk_tool_connections_active_version", "tool_connections", type_="foreignkey")
    op.drop_table("tool_connection_versions")
    op.drop_table("tool_connections")
    op.drop_table("tool_providers")


def downgrade() -> None:
    downgrade_agent_configuration_foundation()
