"""P6B-2a: long-term Agent memory write path (extraction, canonicalization,
consent, conflict). Recall (Chroma vector-outbox consumption, hybrid
semantic/lexical ranking) is P6B-2b, a separate future plan — nothing here
makes a memory recallable."""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"
HEAD = "0019_agent_memory_long_term"


def _scoped_url(schema: str) -> str:
    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema},public', safe='-=,')}"


def _alembic(schema: str, *args, check=True):
    return subprocess.run(
        [sys.executable, "scripts/run_migrations.py", *args],
        cwd=BACKEND_DIR, env=dict(os.environ, DATABASE_URL=_scoped_url(schema)),
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def session():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p6b2a_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", HEAD).returncode == 0
    s = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','a','a@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at) "
        "VALUES ('mc-1','m','llm',NULL,'','openai','[]'::json,'{}'::json,'u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_at) "
        "VALUES ('mcv-1', 'mc-1', 1, 'openai', '{}'::json, :hash, "
        "'[{\"provider_model_revision\": \"test-model\"}]'::json, now())"
    ), {"hash": "0" * 64})
    s.execute(text("UPDATE model_configs SET active_version_id = 'mcv-1' WHERE id = 'mc-1'"))
    app_schema_version_id = s.execute(text(
        "SELECT active_version_id FROM application_state_schema_registries WHERE application_key = 'chat-v1'"
    )).scalar_one()
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('ag-1','private','active','u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, application_state_schema_version_id, config_hash, created_by, created_at) "
        "VALUES ('av-1', 'ag-1', 1, 'test-version', 'mcv-1', 'test-model', '', :svid, 'h', 'u-1', now())"
    ), {"svid": app_schema_version_id})
    s.execute(text("UPDATE agents SET active_version_id = 'av-1' WHERE id = 'ag-1'"))
    s.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES ('sess-1', 'ag-1', 'u-1', 'active', now(), now())"
    ))
    s.commit()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_migration_seeds_predicate_registry(session):
    rows = session.execute(text(
        "SELECT predicate, cardinality FROM agent_memory_predicate_registry ORDER BY predicate"
    )).mappings().all()
    assert {(r["predicate"], r["cardinality"]) for r in rows} == {
        ("user.fact", "multi"), ("user.goal", "multi"), ("user.name", "single"),
        ("user.preference", "multi"), ("user.role", "single"),
    }


def test_agent_memories_predicate_fk_rejects_unknown_predicate(session):
    with pytest.raises(Exception):
        session.execute(text(
            "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
            "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
            "consent_basis, source_spans, status, created_at, updated_at) "
            "VALUES ('m-1', :d, 'ag-1', 'u-1', 'semantic', 'self', 'user.unknown_predicate', "
            "'{}'::jsonb, 'h' || repeat('0', 63), 'x', 0.9, 'low', 'explicit_statement', '[]'::jsonb, "
            "'active', now(), now())"
        ), {"d": DEFAULT_DOMAIN})
        session.commit()
    session.rollback()


def test_agent_memories_partial_unique_active_dedup_key(session):
    def insert(mid, status):
        session.execute(text(
            "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
            "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
            "consent_basis, source_spans, status, created_at, updated_at) "
            "VALUES (:id, :d, 'ag-1', 'u-1', 'semantic', 'self', 'user.name', "
            "'{}'::jsonb, 'h' || repeat('0', 63), 'x', 0.9, 'low', 'explicit_statement', '[]'::jsonb, "
            ":status, now(), now())"
        ), {"id": mid, "d": DEFAULT_DOMAIN, "status": status})

    insert("m-1", "active")
    session.commit()
    # a second row with the SAME dedup key but a non-active status is allowed
    insert("m-2", "deleted")
    session.commit()
    # a second ACTIVE row with the same dedup key is rejected
    with pytest.raises(Exception):
        insert("m-3", "active")
        session.commit()
    session.rollback()


def test_agent_memory_fks_are_restrict_not_cascade(session):
    session.execute(text(
        "INSERT INTO agent_memories (id, security_domain_id, agent_id, user_id, kind, subject_key, "
        "predicate, canonical_value, canonical_value_hash, display_text, confidence, sensitivity, "
        "consent_basis, source_spans, status, created_at, updated_at) "
        "VALUES ('m-1', :d, 'ag-1', 'u-1', 'semantic', 'self', 'user.name', "
        "'{}'::jsonb, 'h' || repeat('0', 63), 'x', 0.9, 'low', 'explicit_statement', '[]'::jsonb, "
        "'active', now(), now())"
    ), {"d": DEFAULT_DOMAIN})
    session.commit()
    with pytest.raises(Exception):
        session.execute(text("DELETE FROM agents WHERE id = 'ag-1'"))
        session.commit()
    session.rollback()


def test_extraction_outbox_state_check_constraint(session):
    session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
        "VALUES ('t-1', 'sess-1', 'succeeded', now(), now())"
    ))
    session.commit()
    with pytest.raises(Exception):
        session.execute(text(
            "INSERT INTO agent_memory_extraction_outbox (id, turn_id, session_id, state, created_at) "
            "VALUES ('eo-1', 't-1', 'sess-1', 'not_a_real_state', now())"
        ))
        session.commit()
    session.rollback()
