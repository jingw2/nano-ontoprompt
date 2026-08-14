"""P2B-CONFIG: immutable agent configuration.

Creating an Agent is one transaction (Agent + AgentVersion v1 + owner grant +
audit).  Every Basic save locks the Agent, clones the full immutable tree,
applies the complete patch, hashes the configuration, inserts N+1 and
CAS-activates under `base_version_no` — old versions stay byte-identical and
there is never a fallback.  Prompt-generation records are sanitized and never
auto-activated.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.auth_service import create_access_token, hash_password


BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p2b_config_red_contract():
    failures = []
    config = BACKEND_DIR / "app" / "services" / "agent" / "configuration.py"
    if not config.exists():
        failures.append("missing app/services/agent/configuration.py")
    else:
        source = config.read_text()
        for symbol in ("create_agent", "save_basic_version", "config_hash"):
            if symbol not in source:
                failures.append(f"configuration.py missing {symbol}")
    pg = BACKEND_DIR / "app" / "services" / "agent" / "prompt_generation.py"
    if not pg.exists():
        failures.append("missing app/services/agent/prompt_generation.py")
    else:
        source = pg.read_text()
        for symbol in ("record_prompt_generation", "accept_prompt_generation"):
            if symbol not in source:
                failures.append(f"prompt_generation.py missing {symbol}")
    if failures:
        pytest.fail("RED_P2B_CONFIG: " + "; ".join(failures))


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
def ctx():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p2b_config_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0005_agent_configuration").returncode == 0
    Session = sessionmaker(bind=create_engine(_scoped_url(schema)))
    with Session() as session:
        actor_id = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
            "VALUES (:id,'cfg-editor','c@t.com','h','editor',true,:d,now(),now())"
        ), {"id": actor_id, "d": DEFAULT_DOMAIN})
        # a model config + immutable version 1 for AgentVersion FK
        session.execute(text(
            "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at) "
            "VALUES (:id,'m','llm',NULL,'','openai','[]'::json,'{}'::json,:owner,now(),now())"
        ), {"id": str(uuid.uuid4()), "owner": actor_id})
        model_id = session.execute(text("SELECT id FROM model_configs LIMIT 1")).scalar_one()
        model_version = str(uuid.uuid4())
        session.execute(text(
            "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_at) "
            "VALUES (:id, :mc, 1, 'openai', '{}'::json, :hash, '[]'::json, now())"
        ), {"id": model_version, "mc": model_id, "hash": "0" * 64})
        session.execute(text(
            "UPDATE model_configs SET active_version_id = :vid, status = 'active' WHERE id = :mc"
        ), {"vid": model_version, "mc": model_id})
        # built-in chat-v1 application state schema version
        app_schema = session.execute(text(
            "SELECT v.id FROM application_state_schema_versions v "
            "JOIN application_state_schema_registries r ON r.active_version_id = v.id "
            "WHERE r.application_key = 'chat-v1'"
        )).scalar_one()
        session.commit()
        yield session, actor_id, model_version, app_schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_create_agent_one_transaction_with_owner_grant(ctx):
    from app.services.agent.configuration import create_agent

    session, actor_id, model_version, app_schema = ctx
    result = create_agent(
        session, actor_id=actor_id, name="Support Agent", description="handles support tickets",
        default_model_config_version_id=model_version, default_model_name="gpt-4o",
        system_prompt="You are a support agent.", memory_settings={"summary": 1200},
        application_state_schema_version_id=app_schema,
    )
    assert result["version_no"] == 1
    assert len(result["config_hash"]) == 64
    # Agent + version + owner grant exist
    assert session.execute(text("SELECT active_version_id FROM agents WHERE id=:id"), {"id": result["agent_id"]}).scalar_one() == result["version_id"]
    grant = session.execute(text(
        "SELECT capabilities FROM agent_access_grants WHERE agent_id=:id AND user_id=:u AND status='active'"
    ), {"id": result["agent_id"], "u": actor_id}).mappings().one()
    assert set(grant["capabilities"]) == {"discover", "run", "view_config", "edit", "view_audit"}
    # audit recorded
    assert session.execute(text(
        "SELECT count(*) FROM governance_audit_outbox WHERE correlation_id LIKE 'ag:%'"
    )).scalar_one() >= 1


def test_save_basic_version_n1_cas_hash_and_old_unchanged(ctx):
    from app.services.agent.configuration import create_agent, save_basic_version

    session, actor_id, model_version, app_schema = ctx
    created = create_agent(
        session, actor_id=actor_id, name="A", description="d1",
        default_model_config_version_id=model_version, default_model_name="gpt-4o",
        system_prompt="p1", memory_settings={}, application_state_schema_version_id=app_schema,
    )
    v2 = save_basic_version(
        session, actor_id=actor_id, agent_id=created["agent_id"], base_version_no=1,
        name="A v2", description="d2", default_model_config_version_id=model_version,
        default_model_name="gpt-4o", system_prompt="p2", memory_settings={"summary": 2000},
        application_state_schema_version_id=app_schema, change_note="tuned prompt",
    )
    assert v2["version_no"] == 2
    assert v2["config_hash"] != created["config_hash"]
    # old version is byte-identical (immutable)
    v1_row = session.execute(text(
        "SELECT name, description, system_prompt, config_hash FROM agent_versions WHERE id=:id"
    ), {"id": created["version_id"]}).mappings().one()
    assert v1_row["name"] == "A" and v1_row["system_prompt"] == "p1"
    assert v1_row["config_hash"] == created["config_hash"]
    # active pointer advanced
    active = session.execute(text(
        "SELECT version_no FROM agent_versions WHERE id = (SELECT active_version_id FROM agents WHERE id=:id)"
    ), {"id": created["agent_id"]}).scalar_one()
    assert active == 2


def test_concurrent_cas_conflict(ctx):
    from app.services.agent.configuration import create_agent, save_basic_version, AgentConfigConflict

    session, actor_id, model_version, app_schema = ctx
    created = create_agent(
        session, actor_id=actor_id, name="A", description="d1",
        default_model_config_version_id=model_version, default_model_name="gpt-4o",
        system_prompt="p1", memory_settings={}, application_state_schema_version_id=app_schema,
    )
    save_basic_version(session, actor_id=actor_id, agent_id=created["agent_id"], base_version_no=1,
                       name="A2", description="d2", default_model_config_version_id=model_version,
                       default_model_name="gpt-4o", system_prompt="p2", memory_settings={},
                       application_state_schema_version_id=app_schema)
    # a second save based on the stale version 1 must fail CAS
    with pytest.raises(AgentConfigConflict):
        save_basic_version(session, actor_id=actor_id, agent_id=created["agent_id"], base_version_no=1,
                           name="A3", description="d3", default_model_config_version_id=model_version,
                           default_model_name="gpt-4o", system_prompt="p3", memory_settings={},
                           application_state_schema_version_id=app_schema)


def test_prompt_generation_records_and_accept(ctx):
    from app.services.agent.configuration import create_agent
    from app.services.agent.prompt_generation import record_prompt_generation, accept_prompt_generation

    session, actor_id, model_version, app_schema = ctx
    created = create_agent(
        session, actor_id=actor_id, name="A", description="d",
        default_model_config_version_id=model_version, default_model_name="gpt-4o",
        system_prompt="p", memory_settings={}, application_state_schema_version_id=app_schema,
    )
    gen = record_prompt_generation(
        session, actor_id=actor_id, agent_id=created["agent_id"], base_version_no=1,
        model_config_version_id=model_version, model_name="gpt-4o",
        input_hash="a" * 64, output_hash="b" * 64,
    )
    assert gen["status"] == "pending"
    accepted = accept_prompt_generation(session, actor_id=actor_id, agent_id=created["agent_id"], generation_id=gen["id"])
    assert accepted["status"] == "accepted"
