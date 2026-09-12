"""0046: backfill v2 mirror rows for legacy Logic rules/Actions.

Two real-world starting states this migration must handle differently:

* `simple_llm`-mode: a legacy row with NO v2 counterpart at all (the
  extraction task never wrote one) -> must get a fresh mirror inserted.
* `pipeline_mapping`-mode: `app/services/v2/mapping/mapping_service.py`
  already dual-writes each rule/action to both tables under two
  INDEPENDENTLY generated ids, correlated only by matching ontology_id+name
  -> the migration must recognize that existing pairing and skip it,
  not insert a second, duplicate v2 row (a first version of this migration
  did exactly that in production before this test existed).
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR, env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "backfill_0046_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0045_ontology_entity_search_depth").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _seed_ontology(conn, ontology_id, name, build_mode):
    conn.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:u,'u','u@t.com','h','editor',true,:d,now(),now()) ON CONFLICT (id) DO NOTHING"
    ), {"u": "00000000-0000-0000-0000-0000000000aa", "d": DEFAULT_DOMAIN})
    conn.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision,build_mode) "
        "VALUES (:id,:name,'test','v1','draft','00000000-0000-0000-0000-0000000000aa',now(),now(),:d,1,:bm)"
    ), {"id": ontology_id, "name": name, "d": DEFAULT_DOMAIN, "bm": build_mode})


def test_backfills_a_simple_llm_rule_with_no_v2_counterpart(schema):
    engine = create_engine(_scoped_url(schema))
    ontology_id = str(uuid.uuid4())
    rule_id = str(uuid.uuid4())
    with engine.begin() as conn:
        _seed_ontology(conn, ontology_id, "Simple LLM Onto", "simple_llm")
        conn.execute(text(
            "INSERT INTO logic_rules (id, ontology_id, name_cn, name_en, description, function_type, definition, "
            "confidence, version, enabled, status, created_at, updated_at, linked_entities) "
            "VALUES (:id, :o, '超额审批规则', 'Rule', 'd', 'derived_property', 'amount > 1000', 0.9, 'v0.1', true, 'draft', now(), now(), '[]')"
        ), {"id": rule_id, "o": ontology_id})

    assert _alembic(schema, "upgrade", "0046_backfill_legacy_logic_action_mirrors").returncode == 0

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, name, logic_type FROM v2_ontology_logic_rules WHERE ontology_id = :o"
        ), {"o": ontology_id}).mappings().all()
    assert len(rows) == 1
    assert rows[0]["id"] == rule_id  # same id as the legacy row -- see legacy_sync.py
    assert rows[0]["name"] == "超额审批规则"
    assert rows[0]["logic_type"] == "derived_property"
    engine.dispose()


def test_skips_a_pipeline_mapping_rule_already_dual_written_under_a_different_id(schema):
    """mapping_service.py's `_upsert_v1_logic`/`_upsert_v2_logic` already
    wrote both rows (independent ids, same ontology_id+name) before this
    migration ever runs -- it must not add a second v2 row for the same
    legacy row."""
    engine = create_engine(_scoped_url(schema))
    ontology_id = str(uuid.uuid4())
    legacy_id = str(uuid.uuid4())
    existing_v2_id = str(uuid.uuid4())
    with engine.begin() as conn:
        _seed_ontology(conn, ontology_id, "Pipeline Mapping Onto", "pipeline_mapping")
        conn.execute(text(
            "INSERT INTO logic_rules (id, ontology_id, name_cn, name_en, description, function_type, definition, "
            "confidence, version, enabled, status, created_at, updated_at, linked_entities) "
            "VALUES (:id, :o, '映射规则: 供应商', 'Rule', 'd', 'mapping', 'x', 0.9, 'v0.1', true, 'draft', now(), now(), '[]')"
        ), {"id": legacy_id, "o": ontology_id})
        conn.execute(text(
            "INSERT INTO v2_ontology_logic_rules (id, ontology_id, name, logic_type, description, expression, "
            "severity, enabled, status, version, created_at, updated_at) "
            "VALUES (:id, :o, '映射规则: 供应商', 'mapping', 'd', '{}', 'info', true, 'draft', 1, now(), now())"
        ), {"id": existing_v2_id, "o": ontology_id})

    assert _alembic(schema, "upgrade", "0046_backfill_legacy_logic_action_mirrors").returncode == 0

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id FROM v2_ontology_logic_rules WHERE ontology_id = :o"
        ), {"o": ontology_id}).mappings().all()
    assert len(rows) == 1  # no duplicate inserted
    assert rows[0]["id"] == existing_v2_id  # the original mapping-service row, untouched
    engine.dispose()


def test_skips_a_pipeline_mapping_action_already_dual_written_under_a_different_id(schema):
    engine = create_engine(_scoped_url(schema))
    ontology_id = str(uuid.uuid4())
    legacy_id = str(uuid.uuid4())
    existing_v2_id = str(uuid.uuid4())
    with engine.begin() as conn:
        _seed_ontology(conn, ontology_id, "Pipeline Mapping Onto 2", "pipeline_mapping")
        conn.execute(text(
            "INSERT INTO actions (id, ontology_id, name_cn, name_en, description, parameters, rules, "
            "submission_criteria, side_effects, linked_entities, linked_logic_ids, confidence, version, "
            "enabled, status, created_at, updated_at) "
            "VALUES (:id, :o, '创建订单', 'Create Order', 'd', '[]', '[]', '[]', '[]', '[]', '[]', 0.9, 'v0.1', true, 'draft', now(), now())"
        ), {"id": legacy_id, "o": ontology_id})
        conn.execute(text(
            "INSERT INTO v2_ontology_action_types (id, ontology_id, name, description, action_category, "
            "parameters, effects, enabled, status, version, created_at, updated_at) "
            "VALUES (:id, :o, '创建订单', 'd', 'crud', '[]', '[]', true, 'draft', 1, now(), now())"
        ), {"id": existing_v2_id, "o": ontology_id})

    assert _alembic(schema, "upgrade", "0046_backfill_legacy_logic_action_mirrors").returncode == 0

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id FROM v2_ontology_action_types WHERE ontology_id = :o"
        ), {"o": ontology_id}).mappings().all()
    assert len(rows) == 1
    assert rows[0]["id"] == existing_v2_id
    engine.dispose()
