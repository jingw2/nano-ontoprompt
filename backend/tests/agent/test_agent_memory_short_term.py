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


def test_count_tokens_openai_model_uses_exact_encoding():
    from app.services.runtime.tokenizer import count_tokens
    # "hello world" is 2 tokens under cl100k_base / o200k_base for gpt-4 family
    assert count_tokens("hello world", "gpt-4o") == 2


def test_count_tokens_non_openai_model_falls_back_to_cl100k():
    from app.services.runtime.tokenizer import count_tokens
    assert count_tokens("hello world", "claude-3-5-sonnet-20241022") == 2


def test_count_tokens_empty_string_is_zero():
    from app.services.runtime.tokenizer import count_tokens
    assert count_tokens("", "gpt-4o") == 0


def test_assemble_bounded_messages_fits_everything_when_small():
    from app.services.runtime.message_budget import assemble_bounded_messages
    result = assemble_bounded_messages(
        system_prompt="You are helpful.", tool_schemas=[], application_state={},
        retrieval_required=[], retrieval_optional=[], summary_text=None,
        recalled_memories=[], history_rows=[{"role": "user", "content": "hi"}],
        pending_interrupt=None, user_message="hi", model_name="gpt-4o",
        budgets={"message_pairs": 12, "summary_token_budget": 1200,
                 "recall_token_budget": 800, "recall_count": 8},
    )
    assert result[0]["role"] == "system"
    assert result[-1] == {"role": "user", "content": "hi"}


def test_assemble_bounded_messages_includes_summary_when_present():
    from app.services.runtime.message_budget import assemble_bounded_messages
    result = assemble_bounded_messages(
        system_prompt="You are helpful.", tool_schemas=[], application_state={},
        retrieval_required=[], retrieval_optional=[], summary_text="prior: discussed X",
        recalled_memories=[], history_rows=[], pending_interrupt=None,
        user_message="continue", model_name="gpt-4o",
        budgets={"message_pairs": 12, "summary_token_budget": 1200,
                 "recall_token_budget": 800, "recall_count": 8},
    )
    assert any("prior: discussed X" in (m.get("content") or "") for m in result)


def test_assemble_bounded_messages_drops_optional_retrieval_before_failing():
    from app.services.runtime.message_budget import assemble_bounded_messages
    # a huge optional-retrieval item alone must not blow the budget when
    # required material still fits comfortably
    huge_optional = "x " * 5000
    # NOTE: 2000, not 200 — RESPONSE_RESERVE_TOKENS is 1024, so a
    # total_budget_tokens below that would blow the system/response-reserve
    # check before the required-vs-optional logic under test ever runs.
    result = assemble_bounded_messages(
        system_prompt="sys", tool_schemas=[], application_state={},
        retrieval_required=[], retrieval_optional=[huge_optional], summary_text=None,
        recalled_memories=[], history_rows=[], pending_interrupt=None,
        user_message="hi", model_name="gpt-4o",
        budgets={"message_pairs": 12, "summary_token_budget": 1200,
                 "recall_token_budget": 800, "recall_count": 8},
        total_budget_tokens=2000,
    )
    assert not any(huge_optional in (m.get("content") or "") for m in result)


def test_assemble_bounded_messages_fails_closed_when_required_material_too_large():
    from app.services.runtime.message_budget import ContextBudgetExceeded, assemble_bounded_messages
    huge_required = "x " * 5000
    with pytest.raises(ContextBudgetExceeded):
        assemble_bounded_messages(
            system_prompt="sys", tool_schemas=[], application_state={},
            retrieval_required=[huge_required], retrieval_optional=[], summary_text=None,
            recalled_memories=[], history_rows=[], pending_interrupt=None,
            user_message="hi", model_name="gpt-4o",
            budgets={"message_pairs": 12, "summary_token_budget": 1200,
                     "recall_token_budget": 800, "recall_count": 8},
            total_budget_tokens=200,
        )


def test_assemble_bounded_messages_orders_history_newest_to_oldest_when_trimmed():
    from app.services.runtime.message_budget import assemble_bounded_messages
    history = [{"role": "user", "content": f"msg{i}"} for i in range(30)]
    result = assemble_bounded_messages(
        system_prompt="sys", tool_schemas=[], application_state={},
        retrieval_required=[], retrieval_optional=[], summary_text=None,
        recalled_memories=[], history_rows=history, pending_interrupt=None,
        user_message="latest", model_name="gpt-4o",
        budgets={"message_pairs": 2, "summary_token_budget": 1200,
                 "recall_token_budget": 800, "recall_count": 8},
    )
    contents = [m["content"] for m in result if m["role"] == "user"]
    # only the newest 2 pairs' worth of history should survive, in original (oldest-first) order
    assert "msg29" in contents[-2] or "msg29" in contents[-1]
    assert "msg0" not in contents


def _seed_messages(session, session_id: str, count: int):
    for i in range(count):
        session.execute(text(
            "INSERT INTO agent_messages (id, session_id, role, ordinal, content, created_at) "
            "VALUES (:id, :sid, :role, :ord, :content, now())"
        ), {"id": f"msg-{session_id}-{i}", "sid": session_id,
            "role": "user" if i % 2 == 0 else "assistant",
            "ord": i, "content": f"message {i}"})
    session.commit()


def test_maybe_regenerate_summary_skips_below_threshold(session, monkeypatch):
    from app.services.memory import summary as summary_module
    _seed_messages(session, "sess-1", 10)  # below default threshold of 24
    called = []
    monkeypatch.setattr(summary_module, "_call_summarizer", lambda *a, **k: called.append(1))
    changed = summary_module.maybe_regenerate_summary(session, session_id="sess-1")
    assert changed is False
    assert called == []


def test_maybe_regenerate_summary_calls_model_above_threshold(session, monkeypatch):
    from app.services.memory import summary as summary_module
    _seed_messages(session, "sess-1", 30)
    monkeypatch.setattr(summary_module, "_call_summarizer", lambda *a, **k: {
        "confirmed_facts": ["the user is investigating order 42"],
        "decisions": [], "unresolved_questions": [], "source_ordinals": [0, 29],
    })
    changed = summary_module.maybe_regenerate_summary(session, session_id="sess-1")
    assert changed is True
    row = session.execute(text(
        "SELECT summary_text, covers_from_ordinal, covers_to_ordinal FROM agent_memory_summaries "
        "WHERE session_id = 'sess-1'"
    )).mappings().one()
    assert "order 42" in row["summary_text"]
    assert row["covers_to_ordinal"] == 29


def test_maybe_regenerate_summary_retains_prior_on_ungrounded_output(session, monkeypatch):
    from app.services.memory import summary as summary_module
    # 50 seeded, existing summary covers up to ordinal 15 -> 34 unsummarized
    # (ordinals 16-49), comfortably above the default threshold of 24, so
    # this test actually reaches the groundedness check rather than
    # returning early on the threshold check (a real earlier draft of this
    # test used only 30 messages, which left just 14 unsummarized — below
    # threshold — and passed for the wrong reason without ever calling the
    # summarizer at all; keep the count high enough that it doesn't regress
    # back to that false-positive shape)
    _seed_messages(session, "sess-1", 50)
    session.execute(text(
        "INSERT INTO agent_memory_summaries "
        "(id, session_id, summary_text, covers_from_ordinal, covers_to_ordinal, "
        "source_message_hash, summary_model_name, summary_token_count, updated_at) "
        "VALUES ('sum-old', 'sess-1', 'the prior good summary', 0, 15, "
        "'h' || repeat('0', 63), 'gpt-4o', 10, now())"
    ))
    session.commit()
    # missing required schema fields -> ungrounded, must retain prior
    monkeypatch.setattr(summary_module, "_call_summarizer", lambda *a, **k: {"garbage": True})
    changed = summary_module.maybe_regenerate_summary(session, session_id="sess-1")
    assert changed is False
    row = session.execute(text(
        "SELECT summary_text FROM agent_memory_summaries WHERE session_id = 'sess-1'"
    )).mappings().one()
    assert row["summary_text"] == "the prior good summary"


def test_maybe_regenerate_summary_noop_when_short_term_disabled(session, monkeypatch):
    from app.services.memory import summary as summary_module
    session.execute(text(
        "UPDATE agent_versions SET memory_settings = '{\"short_term_enabled\": false}'::json WHERE id = 'av-1'"
    ))
    session.commit()
    _seed_messages(session, "sess-1", 30)
    called = []
    monkeypatch.setattr(summary_module, "_call_summarizer", lambda *a, **k: called.append(1))
    changed = summary_module.maybe_regenerate_summary(session, session_id="sess-1")
    assert changed is False
    assert called == []


def test_sweep_processes_eligible_sessions_and_isolates_per_session_errors(session, monkeypatch):
    from app.services.memory import summary as summary_module
    session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES ('sess-2', 'ag-1', 'u-1', 'active', now(), now())"
    ))
    session.commit()
    _seed_messages(session, "sess-1", 30)
    _seed_messages(session, "sess-2", 30)

    calls = []

    def fake_regen(db, *, session_id):
        calls.append(session_id)
        if session_id == "sess-1":
            raise RuntimeError("simulated model failure")
        return True

    monkeypatch.setattr(summary_module, "maybe_regenerate_summary", fake_regen)
    from app.tasks.agent_memory import sweep_memory_summaries
    result = sweep_memory_summaries(db=session)
    assert sorted(calls) == ["sess-1", "sess-2"]
    assert result["errors"] == 1
    assert result["regenerated"] == 1
