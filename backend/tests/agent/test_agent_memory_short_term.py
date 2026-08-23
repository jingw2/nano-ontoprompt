"""P6B-1: short-term Agent memory (rolling summary + deterministic budget)."""
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
HEAD = "0018_agent_memory_short_term"


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
    schema = "p6b1_" + uuid.uuid4().hex
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
        "VALUES ('mcv-1', 'mc-1', 1, 'openai', '{}'::json, :hash, '[]'::json, now())"
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


def test_migration_creates_summaries_table(session):
    row = session.execute(text(
        "INSERT INTO agent_memory_summaries "
        "(id, session_id, summary_text, covers_from_ordinal, covers_to_ordinal, "
        "source_message_hash, summary_model_name, summary_token_count, updated_at) "
        "VALUES ('sum-1', 'sess-1', 'the user asked about X', 1, 24, "
        "'h' || repeat('0', 63), 'gpt-4o', 120, now()) RETURNING id"
    )).scalar_one()
    assert row == "sum-1"


def test_summaries_session_id_is_unique(session):
    session.execute(text(
        "INSERT INTO agent_memory_summaries "
        "(id, session_id, summary_text, covers_from_ordinal, covers_to_ordinal, "
        "source_message_hash, summary_model_name, summary_token_count, updated_at) "
        "VALUES ('sum-1', 'sess-1', 'first', 1, 10, 'h' || repeat('0', 63), 'gpt-4o', 50, now())"
    ))
    session.commit()
    with pytest.raises(Exception):
        session.execute(text(
            "INSERT INTO agent_memory_summaries "
            "(id, session_id, summary_text, covers_from_ordinal, covers_to_ordinal, "
            "source_message_hash, summary_model_name, summary_token_count, updated_at) "
            "VALUES ('sum-2', 'sess-1', 'second', 1, 20, 'i' || repeat('0', 63), 'gpt-4o', 60, now())"
        ))
        session.commit()
    session.rollback()


def test_summaries_session_fk_is_restrict_not_cascade(session):
    session.execute(text(
        "INSERT INTO agent_memory_summaries "
        "(id, session_id, summary_text, covers_from_ordinal, covers_to_ordinal, "
        "source_message_hash, summary_model_name, summary_token_count, updated_at) "
        "VALUES ('sum-1', 'sess-1', 'x', 1, 10, 'h' || repeat('0', 63), 'gpt-4o', 50, now())"
    ))
    session.commit()
    with pytest.raises(Exception):
        session.execute(text("DELETE FROM agent_sessions WHERE id = 'sess-1'"))
        session.commit()
    session.rollback()


def test_memory_settings_fills_defaults_on_empty_input():
    from app.services.agent.memory_settings import validate_memory_settings
    settings = validate_memory_settings({})
    assert settings == {
        "short_term_enabled": True,
        "long_term_enabled": False,
        "message_pairs": 12,
        "summary_threshold": 24,
        "summary_token_budget": 1200,
        "recall_token_budget": 800,
        "recall_count": 8,
    }


def test_memory_settings_accepts_values_within_range():
    from app.services.agent.memory_settings import validate_memory_settings
    settings = validate_memory_settings({
        "short_term_enabled": False, "long_term_enabled": True,
        "message_pairs": 20, "summary_threshold": 40,
        "summary_token_budget": 2048, "recall_token_budget": 1200, "recall_count": 12,
    })
    assert settings["message_pairs"] == 20
    assert settings["long_term_enabled"] is True


@pytest.mark.parametrize("key,bad_value", [
    ("message_pairs", 1), ("message_pairs", 21),
    ("summary_threshold", 7), ("summary_threshold", 41),
    ("summary_token_budget", 255), ("summary_token_budget", 2049),
    ("recall_token_budget", 127), ("recall_token_budget", 1201),
    ("recall_count", 0), ("recall_count", 13),
])
def test_memory_settings_rejects_out_of_range(key, bad_value):
    from app.services.agent.memory_settings import MemorySettingsError, validate_memory_settings
    with pytest.raises(MemorySettingsError):
        validate_memory_settings({key: bad_value})


def test_memory_settings_rejects_unknown_key():
    from app.services.agent.memory_settings import MemorySettingsError, validate_memory_settings
    with pytest.raises(MemorySettingsError):
        validate_memory_settings({"unknown_key": 1})


def test_memory_settings_rejects_wrong_type():
    from app.services.agent.memory_settings import MemorySettingsError, validate_memory_settings
    with pytest.raises(MemorySettingsError):
        validate_memory_settings({"message_pairs": "twelve"})
