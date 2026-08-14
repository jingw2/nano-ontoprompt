"""P3A-TURNDB: Section 6 runtime schema + derived-index outbox trigger.

The 0006 revision adds the session/turn/message graph without FK cycles, the
one-active-Turn partial unique index, the transactional turn dispatch outbox,
and — on authoritative instance/relation rows — a PostgreSQL trigger that
emits release-aware upsert/delete events into `agent_index_outbox`, so
router, mapping and import writers cannot bypass the derived index.
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


def test_p3a_turndb_red_contract():
    failures = []
    if not MIGRATION.exists():
        failures.append("missing alembic/versions/0006_agent_runtime.py")
    else:
        source = MIGRATION.read_text()
        for symbol in ("upgrade_runtime_foundation", "upgrade_runtime_artifact_schema",
                       "upgrade_derived_index_outbox", "emit_agent_index_outbox"):
            if symbol not in source:
                failures.append(f"0006 missing {symbol}")
    model = BACKEND_DIR / "app" / "models" / "agent_runtime.py"
    if not model.exists():
        failures.append("missing app/models/agent_runtime.py")
    if failures:
        pytest.fail("RED_P3A_TURNDB: " + "; ".join(failures))


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
    schema = "p3a_turndb_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _connection(schema: str):
    return create_engine(_scoped_url(schema))


def _seed(schema: str, connection, *, agent_id: str | None = None, users=("seed-u",)) -> None:
    connection.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:id,'s','s@t.com','h','admin',true,:d,now(),now())"
    ), {"id": users[0], "d": DEFAULT_DOMAIN})
    connection.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-1','O','test','v1','created',:u,now(),now(),:d,1)"
    ), {"u": users[0], "d": DEFAULT_DOMAIN})
    connection.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES (:id,'private','active',:u,now(),now())"
    ), {"id": agent_id or "a-1", "u": users[0]})


def test_fresh_0006_runtime_schema_and_fk_graph(full_schema):
    """Sessions/turns/messages exist with the Section 6 FK graph (turn message
    links and the session active pointer added after the base tables), plus the
    full step-6 runtime table inventory that no later packet may add."""
    _alembic(full_schema, "upgrade", "0006_agent_runtime")
    engine = _connection(full_schema)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table in ("agent_sessions", "agent_turns", "agent_messages",
                  "agent_turn_dispatch_outbox", "agent_index_outbox"):
        assert table in tables, table
    for table in ("runtime_artifacts", "agent_turn_checkpoints",
                  "agent_turn_checkpoint_writes", "agent_node_executions",
                  "agent_model_invocations", "agent_runtime_events", "agent_approvals",
                  "agent_reconciliation_cases", "agent_clarification_requests",
                  "agent_application_state_snapshots", "agent_tool_executions",
                  "agent_stream_tickets", "agent_purge_jobs", "agent_purge_markers"):
        assert table in tables, f"Section 6 step-6 table missing: {table}"

    fk_turns = {fk["name"]: fk for fk in inspector.get_foreign_keys("agent_turns")}
    turn_targets = {fk["referred_table"] for fk in fk_turns.values()}
    assert "agent_sessions" in turn_targets
    assert "agent_messages" in turn_targets  # request/response FKs added by ALTER
    fk_messages = {fk["name"]: fk for fk in inspector.get_foreign_keys("agent_messages")}
    assert {fk["referred_table"] for fk in fk_messages.values()} == {"agent_sessions", "agent_turns"}
    fk_sessions = {fk["name"]: fk for fk in inspector.get_foreign_keys("agent_sessions")}
    assert "agent_turns" in {fk["referred_table"] for fk in fk_sessions.values()}  # active pointer SET NULL
    fk_do = {fk["name"]: fk for fk in inspector.get_foreign_keys("agent_turn_dispatch_outbox")}
    assert "agent_turns" in {fk["referred_table"] for fk in fk_do.values()}

    indexes = {i["name"]: i for i in inspector.get_indexes("agent_turns")}
    assert "uq_agent_turns_active_session" in indexes
    assert indexes["uq_agent_turns_active_session"]["unique"] is True
    engine.dispose()


def test_active_turn_uniqueness_enforced(full_schema):
    """The partial unique index rejects a second active Turn for one session."""
    _alembic(full_schema, "upgrade", "0006_agent_runtime")
    engine = _connection(full_schema)
    with engine.begin() as connection:
        _seed(full_schema, connection)
        connection.execute(text(
            "INSERT INTO agent_sessions (id,agent_id,owner_user_id,status) "
            "VALUES ('s-1','a-1','seed-u','active')"
        ))
        connection.execute(text(
            "INSERT INTO agent_turns (id,session_id,status) VALUES ('t-1','s-1','queued')"
        ))
        # second active turn -> partial unique violation
        with pytest.raises(Exception) as excinfo:
            connection.execute(text(
                "INSERT INTO agent_turns (id,session_id,status) VALUES ('t-2','s-1','running')"
            ))
        assert "uq_agent_turns_active_session" in str(excinfo.value)
    engine.dispose()


def test_active_turn_unique_allows_terminal_then_new(full_schema):
    """A terminal Turn releases the active slot so a new Turn may start."""
    _alembic(full_schema, "upgrade", "0006_agent_runtime")
    engine = _connection(full_schema)
    with engine.begin() as connection:
        _seed(full_schema, connection)
        connection.execute(text(
            "INSERT INTO agent_sessions (id,agent_id,owner_user_id,status) "
            "VALUES ('s-1','a-1','seed-u','active')"
        ))
        connection.execute(text(
            "INSERT INTO agent_turns (id,session_id,status) VALUES ('t-1','s-1','running')"
        ))
        connection.execute(text(
            "UPDATE agent_turns SET status = 'succeeded' WHERE id = 't-1'"
        ))
        connection.execute(text(
            "INSERT INTO agent_turns (id,session_id,status) VALUES ('t-2','s-1','queued')"
        ))
    engine.dispose()


def test_index_outbox_trigger_emits_instance_events(full_schema):
    """Direct SQL mutations to authoritative instance rows emit exactly one
    release-aware upsert/delete event each (writers cannot bypass)."""
    _alembic(full_schema, "upgrade", "0006_agent_runtime")
    engine = _connection(full_schema)
    with engine.begin() as connection:
        _seed(full_schema, connection)
        connection.execute(text(
            "INSERT INTO entities (id,ontology_id,name_cn,name_en,properties,confidence,version,created_at,updated_at) "
            "VALUES ('e-1','o-1','实体','E','{}'::json,0.9,'v1',now(),now())"
        ))
        connection.execute(text(
            "INSERT INTO entity_instances (id,entity_id,ontology_id,row_identity,row_data,created_at) "
            "VALUES ('i-1','e-1','o-1','row-1','{}'::json,now())"
        ))
        upserts = connection.execute(text(
            "SELECT event_type FROM agent_index_outbox WHERE instance_id = 'i-1' ORDER BY created_at"
        )).scalars().all()
        assert upserts == ["upsert_instance"]
        # update bumps revision -> another upsert event
        connection.execute(text(
            "UPDATE entity_instances SET revision = 2, updated_at = now() WHERE id = 'i-1'"
        ))
        connection.execute(text(
            "UPDATE entity_instances SET deleted_at = now(), updated_at = now() WHERE id = 'i-1'"
        ))
        connection.execute(text(
            "DELETE FROM entity_instances WHERE id = 'i-1'"
        ))
        events = connection.execute(text(
            "SELECT event_type FROM agent_index_outbox WHERE instance_id = 'i-1' ORDER BY created_at"
        )).scalars().all()
        assert events == ["upsert_instance", "upsert_instance", "delete_instance", "delete_instance"]
    engine.dispose()


def test_index_outbox_trigger_emits_edge_events(full_schema):
    """Relation-edge mutations emit upsert/delete edge events transactionally."""
    _alembic(full_schema, "upgrade", "0006_agent_runtime")
    engine = _connection(full_schema)
    with engine.begin() as connection:
        _seed(full_schema, connection)
        connection.execute(text(
            "INSERT INTO entities (id,ontology_id,name_cn,name_en,properties,confidence,version,created_at,updated_at) "
            "VALUES ('e-1','o-1','实体','E','{}'::json,0.9,'v1',now(),now()),"
            "('e-2','o-1','实体2','E2','{}'::json,0.9,'v1',now(),now())"
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
        connection.execute(text(
            "UPDATE entity_instance_relations SET deleted_at = now(), updated_at = now() WHERE id = 'ir-1'"
        ))
        connection.execute(text(
            "DELETE FROM entity_instance_relations WHERE id = 'ir-1'"
        ))
        events = connection.execute(text(
            "SELECT event_type FROM agent_index_outbox WHERE edge_id = 'ir-1' ORDER BY created_at"
        )).scalars().all()
        assert events == ["upsert_edge", "delete_edge", "delete_edge"]
        # events carry the edge endpoints for the release-aware consumer
        row = connection.execute(text(
            "SELECT source_instance_id, target_instance_id, relation_definition_id, ontology_id "
            "FROM agent_index_outbox WHERE edge_id = 'ir-1' LIMIT 1"
        )).mappings().one()
        assert (row["source_instance_id"], row["target_instance_id"], row["relation_definition_id"]) == ("i-1", "i-2", "r-1")
        assert row["ontology_id"] == "o-1"
    engine.dispose()


def test_index_outbox_trigger_fires_within_one_transaction(full_schema):
    """The outbox row is created in the same transaction as the mutation —
    rollback removes both, so consumers never see a mutation without its event."""
    _alembic(full_schema, "upgrade", "0006_agent_runtime")
    engine = _connection(full_schema)
    with engine.begin() as connection:
        _seed(full_schema, connection)
        connection.execute(text(
            "INSERT INTO entities (id,ontology_id,name_cn,name_en,properties,confidence,version,created_at,updated_at) "
            "VALUES ('e-1','o-1','实体','E','{}'::json,0.9,'v1',now(),now())"
        ))
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO entity_instances (id,entity_id,ontology_id,row_identity,row_data,created_at) "
            "VALUES ('i-1','e-1','o-1','row-1','{}'::json,now())"
        ))
        assert connection.execute(text(
            "SELECT count(*) FROM agent_index_outbox WHERE instance_id = 'i-1'"
        )).scalar_one() == 1
        connection.rollback()
    with engine.begin() as connection:
        assert connection.execute(text(
            "SELECT count(*) FROM agent_index_outbox WHERE instance_id = 'i-1'"
        )).scalar_one() == 0
    engine.dispose()


def test_migration_0006_calls_turndb_helpers_in_order(monkeypatch):
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


def test_0006_downgrade_drops_runtime_and_outbox(full_schema):
    _alembic(full_schema, "upgrade", "0006_agent_runtime")
    result = _alembic(full_schema, "downgrade", "0005_agent_configuration")
    assert result.returncode == 0, result.stderr
    engine = _connection(full_schema)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table in ("agent_index_outbox", "agent_turn_dispatch_outbox", "agent_messages",
                  "agent_turns", "agent_sessions", "runtime_artifacts",
                  "agent_turn_checkpoints", "agent_turn_checkpoint_writes",
                  "agent_node_executions", "agent_model_invocations", "agent_runtime_events",
                  "agent_approvals", "agent_reconciliation_cases", "agent_clarification_requests",
                  "agent_application_state_snapshots", "agent_tool_executions",
                  "agent_stream_tickets", "agent_purge_jobs", "agent_purge_markers"):
        assert table not in tables, table
    engine.dispose()
