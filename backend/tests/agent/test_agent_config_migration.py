"""P2B-SCHEMA: additive agent configuration schema (0005).

Migration `0005_agent_configuration` creates the Agent/version/grant/policy/
retrieval-source/application-state-schema/provider/connection/prompt-provenance
tables, backfills no Agent automatically, and idempotently seeds the built-in
`chat-v1` application-state schema.  Populated upgrade and empty downgrade
must pass; every FK is RESTRICT and no immutable version row is updatable.

PostgreSQL-marked tests use TEST_DATABASE_URL; SQLite never substitutes.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, inspect, text


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND_DIR / "alembic" / "versions" / "0005_agent_configuration.py"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"

NEW_0005_TABLES = {
    "agents",
    "agent_versions",
    "agent_access_grants",
    "ontology_data_grants",
    "application_state_schema_registries",
    "application_state_schema_versions",
    "tool_providers",
    "tool_connections",
    "tool_connection_versions",
    "prompt_generations",
    "agent_ontology_bindings",
    "agent_external_tool_bindings",
    "agent_retrieval_sources",
}


def test_p2b_schema_red_contract():
    failures = []
    if not MIGRATION.exists():
        failures.append("missing alembic/versions/0005_agent_configuration.py")
    else:
        source = MIGRATION.read_text()
        for helper in ("upgrade_agent_configuration_foundation", "seed_application_state_schemas", "downgrade_agent_configuration_foundation"):
            if helper not in source:
                failures.append(f"0005 missing {helper}")
    agent_model = BACKEND_DIR / "app" / "models" / "agent.py"
    if not agent_model.exists():
        failures.append("missing app/models/agent.py")
    else:
        source = agent_model.read_text()
        for symbol in ("Agent", "AgentVersion", "AgentAccessGrant"):
            if symbol not in source:
                failures.append(f"agent.py missing {symbol}")
    config_model = BACKEND_DIR / "app" / "models" / "agent_config.py"
    if not config_model.exists():
        failures.append("missing app/models/agent_config.py")
    else:
        source = config_model.read_text()
        for symbol in ("ApplicationStateSchemaRegistry", "ToolConnectionVersion", "PromptGeneration"):
            if symbol not in source:
                failures.append(f"agent_config.py missing {symbol}")
    if failures:
        pytest.fail("RED_P2B_SCHEMA: " + "; ".join(failures))


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema}', safe='-=')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR,
        env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def full_schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p2b_schema_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _connection(schema: str):
    return create_engine(_scoped_url(schema))


def test_fresh_0005_upgrade_installs_schema_and_seed(full_schema):
    result = _alembic(full_schema, "upgrade", "0005_agent_configuration")
    assert result.returncode == 0, result.stderr
    engine = _connection(full_schema)
    inspector = inspect(engine)
    migrated = set(inspector.get_table_names())
    assert NEW_0005_TABLES <= migrated
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0005_agent_configuration"
        # built-in chat-v1 registry seed: registry + immutable version 1 + active pointer
        registry = connection.execute(text(
            "SELECT id, application_key, status, active_version_id "
            "FROM application_state_schema_registries WHERE application_key = 'chat-v1'"
        )).mappings().one()
        assert registry["status"] == "active"
        version = connection.execute(text(
            "SELECT version_no, canonical_hash, json_schema "
            "FROM application_state_schema_versions WHERE id = :vid"
        ), {"vid": registry["active_version_id"]}).mappings().one()
        assert version["version_no"] == 1
        assert version["json_schema"]["type"] == "object"
        assert len(version["canonical_hash"]) == 64
        # immutable version rows are RESTRICT: a version cannot be deleted while the registry pointer holds it
        with pytest.raises(Exception):
            connection.execute(text(
                "DELETE FROM application_state_schema_versions WHERE id = :vid"
            ), {"vid": registry["active_version_id"]})
        connection.rollback()  # PG aborts the transaction on the FK error
        # no agents are backfilled automatically
        assert connection.execute(text("SELECT count(*) FROM agents")).scalar_one() == 0
    engine.dispose()


def test_0005_fk_immutability_restrict_on_all_versions(full_schema):
    _alembic(full_schema, "upgrade", "0005_agent_configuration")
    engine = _connection(full_schema)
    inspector = inspect(engine)
    for table in ("agent_versions", "application_state_schema_versions", "tool_connection_versions"):
        fks = inspector.get_foreign_keys(table)
        assert any(fk.get("options", {}).get("ondelete") == "RESTRICT" for fk in fks), f"{table} lacks RESTRICT FK"
    engine.dispose()


def test_downgrade_0005_strict_reverse(full_schema):
    _alembic(full_schema, "upgrade", "0005_agent_configuration")
    result = _alembic(full_schema, "downgrade", "0004_roles_model_versions")
    assert result.returncode == 0, result.stderr
    engine = _connection(full_schema)
    migrated = set(inspect(engine).get_table_names())
    assert NEW_0005_TABLES.isdisjoint(migrated)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0004_roles_model_versions"
    engine.dispose()


def test_migration_0005_calls_helpers_in_normative_order(monkeypatch):
    spec = importlib.util.spec_from_file_location("migration_0005_agent", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    for helper in ("upgrade_agent_configuration_foundation", "seed_application_state_schemas"):
        monkeypatch.setattr(module, helper, (lambda name: lambda: calls.append(name))(helper))
    module.upgrade()
    assert calls == ["upgrade_agent_configuration_foundation", "seed_application_state_schemas"]
    calls.clear()
    for helper in ("downgrade_agent_configuration_foundation",):
        monkeypatch.setattr(module, helper, (lambda name: lambda: calls.append(name))(helper))
    module.downgrade()
    assert calls == ["downgrade_agent_configuration_foundation"]


def test_agent_orm_models_register_in_registry(full_schema):
    from app.database import Base
    from app.models import load_all_models

    load_all_models()
    metadata = set(Base.metadata.tables)
    assert NEW_0005_TABLES <= metadata
