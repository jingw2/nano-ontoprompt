"""P3A-INSTANCE: authoritative instance revisions + restricted relations.

EntityInstance gains revision/updated_at/deleted_at and a unique
(ontology_id, entity_id, row_identity) identity (created only when the
duplicate preflight is clean — duplicates are reported, never silently
deduped); `entity_id` FK becomes RESTRICT; Entity/Relation gain soft
deprecation columns; `entity_instance_relations` is created with RESTRICT FKs
and a partial unique active-edge index; the delete guard (I-1 deferral) now
rejects relation-definition deletes referenced by instance edges.
"""
import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, inspect, text


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND_DIR / "alembic" / "versions" / "0006_agent_runtime.py"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p3a_instance_red_contract():
    failures = []
    if not MIGRATION.exists():
        failures.append("missing alembic/versions/0006_agent_runtime.py")
    else:
        source = MIGRATION.read_text()
        for symbol in ("upgrade_instance_revision_foundation", "preflight_instance_duplicates", "upgrade_instance_edge_guards"):
            if symbol not in source:
                failures.append(f"0006 missing {symbol}")
    model = BACKEND_DIR / "app" / "models" / "entity_instance_relation.py"
    if not model.exists():
        failures.append("missing app/models/entity_instance_relation.py")
    if failures:
        pytest.fail("RED_P3A_INSTANCE: " + "; ".join(failures))


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


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
    schema = "p3a_instance_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _connection(schema: str):
    return create_engine(_scoped_url(schema))


def test_fresh_0006_upgrade_schema_and_no_cascade(full_schema):
    result = _alembic(full_schema, "upgrade", "0006_agent_runtime")
    assert result.returncode == 0, result.stderr
    engine = _connection(full_schema)
    inspector = inspect(engine)
    migrated = set(inspector.get_table_names())
    assert "entity_instance_relations" in migrated
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0006_agent_runtime"
        cols = {c["name"] for c in inspector.get_columns("entity_instances")}
        assert {"revision", "updated_at", "deleted_at"} <= cols
        # unique identity index present on a clean schema
        indexes = {i["name"]: i for i in inspector.get_indexes("entity_instances")}
        assert "uq_entity_instances_identity" in indexes and indexes["uq_entity_instances_identity"]["unique"]
        # entity_id FK is RESTRICT (no cascade of authoritative data)
        fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("entity_instances")}
        assert fks["entity_instances_entity_id_fkey"]["options"]["ondelete"] == "RESTRICT"
        assert all(fk["options"]["ondelete"] == "RESTRICT" for fk in inspector.get_foreign_keys("entity_instance_relations"))
        # soft-deprecation columns on definitions
        assert "deprecated_at" in {c["name"] for c in inspector.get_columns("entities")}
        assert "deprecated_at" in {c["name"] for c in inspector.get_columns("relations")}
        # active-edge partial unique index
        eir_indexes = {i["name"] for i in inspector.get_indexes("entity_instance_relations")}
        assert "uq_entity_instance_relations_active_edge" in eir_indexes
    engine.dispose()


def test_duplicate_preflight_reports_without_aborting_ddl(full_schema):
    _alembic(full_schema, "upgrade", "0005_agent_configuration")
    engine = _connection(full_schema)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES ('seed-u','s','s@t.com','h','admin',true,:d,now(),now())"
        ), {"d": DEFAULT_DOMAIN})
        connection.execute(text(
            "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
            "VALUES ('o-1','O','test','v1','created','seed-u',now(),now(),:d,1)"
        ), {"d": DEFAULT_DOMAIN})
        connection.execute(text(
            "INSERT INTO entities (id,ontology_id,name_cn,name_en,properties,confidence,version,created_at,updated_at) "
            "VALUES ('e-1','o-1','实体','E','{}'::json,0.9,'v1',now(),now())"
        ))
        connection.execute(text(
            "INSERT INTO entity_instances (id,entity_id,ontology_id,row_identity,row_data,created_at) "
            "VALUES ('i-1','e-1','o-1','dup','{}'::json,now()),('i-2','e-1','o-1','dup','{}'::json,now())"
        ))
    engine.dispose()
    result = _alembic(full_schema, "upgrade", "0006_agent_runtime")
    assert result.returncode == 0, result.stderr  # never aborts on duplicates
    engine = _connection(full_schema)
    with engine.connect() as connection:
        from app.services.publication import cutover  # noqa
        spec = importlib.util.spec_from_file_location("migration_0006", MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        duplicates = module.preflight_instance_duplicates(connection)
        assert any(d["row_identity"] == "dup" and d["cnt"] == 2 for d in duplicates)
        indexes = {i["name"]: i for i in inspect(engine).get_indexes("entity_instances")}
        assert "uq_entity_instances_identity" not in indexes  # deferred while duplicates exist
        assert "ix_entity_instances_identity" in indexes
    engine.dispose()


def test_delete_guard_rejects_relation_with_instance_edges(full_schema):
    """I-1 deferral: after 0006, a relation definition referenced by an
    authoritative instance edge cannot be deleted (DEFINITION_IN_USE)."""
    _alembic(full_schema, "upgrade", "0006_agent_runtime")
    engine = _connection(full_schema)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES ('seed-u','s','s@t.com','h','admin',true,:d,now(),now())"
        ), {"d": DEFAULT_DOMAIN})
        connection.execute(text(
            "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
            "VALUES ('o-1','O','test','v1','created','seed-u',now(),now(),:d,1)"
        ), {"d": DEFAULT_DOMAIN})
        connection.execute(text(
            "INSERT INTO entities (id,ontology_id,name_cn,name_en,properties,confidence,version,created_at,updated_at) "
            "VALUES ('e-1','o-1','实体','E','{}'::json,0.9,'v1',now(),now()),('e-2','o-1','实体2','E2','{}'::json,0.9,'v1',now(),now())"
        ))
        connection.execute(text(
            "INSERT INTO relations (id,ontology_id,type,source_entity,target_entity,properties,confidence,created_at) "
            "VALUES ('r-1','o-1','related','e-1','e-2','{}'::json,0.9,now())"
        ))
        connection.execute(text(
            "INSERT INTO entity_instances (id,entity_id,ontology_id,row_identity,row_data,created_at) "
            "VALUES ('i-1','e-1','o-1','a','{}'::json,now()),('i-2','e-2','o-1','b','{}'::json,now())"
        ))
        connection.execute(text(
            "INSERT INTO entity_instance_relations (id,ontology_id,source_instance_id,target_instance_id,relation_definition_id,properties,revision,created_at,updated_at) "
            "VALUES ('ir-1','o-1','i-1','i-2','r-1','{}'::json,1,now(),now())"
        ))
        # latch so the guard is active (singleton id, NOT NULL actor/hash columns)
        connection.execute(text(
            "INSERT INTO publication_activation_latch (id, activated_by, build_manifest_hash) "
            "VALUES ('00000000-0000-0000-0000-00000000000c', 'seed-u', 'h')"
        ))
        with pytest.raises(Exception) as excinfo:
            connection.execute(text("DELETE FROM relations WHERE id = 'r-1'"))
        assert "DEFINITION_IN_USE" in str(excinfo.value)
    engine.dispose()


def test_migration_0006_calls_helpers_in_order(monkeypatch):
    spec = importlib.util.spec_from_file_location("migration_0006", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    for helper in ("upgrade_instance_revision_foundation", "upgrade_instance_edge_guards",
                   "upgrade_runtime_foundation", "upgrade_runtime_artifact_schema",
                   "upgrade_derived_index_outbox"):
        monkeypatch.setattr(module, helper, (lambda name: lambda: calls.append(name))(helper))
    module.upgrade()
    assert calls == ["upgrade_instance_revision_foundation", "upgrade_instance_edge_guards",
                     "upgrade_runtime_foundation", "upgrade_runtime_artifact_schema",
                     "upgrade_derived_index_outbox"]
    calls.clear()
    for helper in ("downgrade_derived_index_outbox", "downgrade_runtime_foundation",
                   "downgrade_runtime_artifact_schema", "downgrade_instance_edge_guards",
                   "downgrade_instance_revision_foundation"):
        monkeypatch.setattr(module, helper, (lambda name: lambda: calls.append(name))(helper))
    module.downgrade()
    assert calls == ["downgrade_derived_index_outbox", "downgrade_runtime_artifact_schema",
                     "downgrade_runtime_foundation", "downgrade_instance_edge_guards",
                     "downgrade_instance_revision_foundation"]
