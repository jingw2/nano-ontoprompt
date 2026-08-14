"""P3B-STATEAUDIT: Agent application-state + scoped audit.

Schema-validated snapshot/patch with (base_revision, base_hash) CAS, and
read-only audit list/detail scoped to the caller's security domain and the
agent/turn correlation.  No audit write route exists.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.routers.agent_application_state import router
from app.services.auth_service import create_access_token


BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p3b_stateaudit_red_contract():
    failures = []
    for path in ("app/services/runtime/application_state.py",
                 "app/routers/agent_application_state.py",
                 "app/routers/agent_audit.py",
                 "app/services/agent_audit_query.py",
                 "app/schemas/agent_application_state.py",
                 "app/schemas/agent_audit.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    svc = BACKEND_DIR / "app" / "services" / "runtime" / "application_state.py"
    if svc.exists():
        for symbol in ("get_snapshot", "patch_snapshot"):
            if symbol not in svc.read_text():
                failures.append(f"application_state.py missing {symbol}")
    if failures:
        pytest.fail("RED_P3B_STATEAUDIT: " + "; ".join(failures))


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
def schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p3b_stateaudit_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0006_agent_runtime").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _session(schema):
    return sessionmaker(bind=create_engine(_scoped_url(schema)))()


def _client(session):
    from app.deps import get_db

    def override_get_db():
        yield session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


def _seed(session, *, editor_id="u-1", agent_id="a-1"):
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:u,'s','s@t.com','h','editor',true,:d,now(),now())"
    ), {"u": editor_id, "d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-1','O','test','v1','created',:u,now(),now(),:d,1)"
    ), {"u": editor_id, "d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES (:id,'private','active',:u,now(),now())"
    ), {"id": agent_id, "u": editor_id})
    session.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, created_by, created_at, updated_at) "
        "VALUES (:id, :agent, :u, CAST(:caps AS json), 1, 'active', :u, now(), now())"
    ), {"id": str(uuid.uuid4()), "agent": agent_id, "u": editor_id,
        "caps": '["discover", "run", "view_config", "edit", "view_audit"]'})
    # use the built-in chat-v1 schema pinned by 0005
    schema_id = session.execute(text(
        "SELECT v.id FROM application_state_schema_versions v "
        "JOIN application_state_schema_registries r ON r.active_version_id = v.id "
        "WHERE r.application_key = 'chat-v1'"
    )).scalar_one()
    session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) "
        "VALUES ('s-1', :agent, :u, 'active')"
    ), {"agent": agent_id, "u": editor_id})
    # agent version pinned to the schema + active pointer + model identity
    session.execute(text(
        "INSERT INTO model_configs (id,name,config_type,api_base,api_key_encrypted,provider,models,options,created_by,created_at,updated_at) "
        "VALUES ('m-1','m','llm',NULL,'','openai','[]'::json,'{}'::json,:owner,now(),now())"
    ), {"owner": editor_id})
    session.execute(text(
        "INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_at) "
        "VALUES ('mv-1','m-1',1,'openai','{}'::json,:hash,'[]'::json,now())"
    ), {"hash": "0" * 64})
    session.execute(text("UPDATE model_configs SET active_version_id = 'mv-1' WHERE id = 'm-1'"))
    session.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, memory_settings, application_state_schema_version_id, "
        "config_hash, created_by, created_at) "
        "VALUES ('v-1', :agent, 1, 'A', 'mv-1', 'gpt-4o', 'p', '{}'::json, :svid, :hash, :u, now())"
    ), {"agent": agent_id, "svid": schema_id, "hash": "a" * 64, "u": editor_id})
    session.execute(text(
        "UPDATE agents SET active_version_id = 'v-1' WHERE id = :agent"
    ), {"agent": agent_id})
    session.commit()


def test_state_patch_cas_and_get(schema):
    session = _session(schema)
    _seed(session)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            # initial snapshot -> empty, revision 0
            r = c.get("/api/v1/agent-sessions/s-1/application-state", headers=headers)
            assert r.status_code == 200
            assert r.json()["data"]["revision"] == 0
            assert r.json()["data"]["state"] == {}
            # patch -> 201 with new revision/hash
            r = c.post("/api/v1/agent-sessions/s-1/application-state",
                       json={"base_revision": 0, "base_hash": "", "patch": {"locale": "zh-CN"}},
                       headers=headers)
            assert r.status_code == 201
            data = r.json()["data"]
            assert data["revision"] == 1
            assert len(data["hash"]) == 64
            assert data["state"] == {"locale": "zh-CN"}
            # stale base -> 409
            r = c.post("/api/v1/agent-sessions/s-1/application-state",
                       json={"base_revision": 0, "base_hash": "", "patch": {"locale": "en"}},
                       headers=headers)
            assert r.status_code == 409
            assert r.json()["detail"] == "APPLICATION_STATE_CONFLICT"
            # unknown key -> 422
            r = c.post("/api/v1/agent-sessions/s-1/application-state",
                       json={"base_revision": 1, "base_hash": data["hash"], "patch": {"nope": 1}},
                       headers=headers)
            assert r.status_code == 422
    finally:
        session.close()


def test_state_requires_run_grant(schema):
    session = _session(schema)
    _seed(session)
    stranger_id = str(uuid.uuid4())
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:id,'st','st@t.com','h','editor',true,:d,now(),now())"
    ), {"id": stranger_id, "d": DEFAULT_DOMAIN})
    session.commit()
    stranger_headers = {"Authorization": f"Bearer {create_access_token({'sub': stranger_id, 'role': 'editor'})}"}
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            assert c.get("/api/v1/agent-sessions/s-1/application-state", headers=stranger_headers).status_code == 404
            assert c.post("/api/v1/agent-sessions/s-1/application-state",
                          json={"base_revision": 0, "base_hash": "", "patch": {}},
                          headers=stranger_headers).status_code == 404
    finally:
        session.close()


def test_audit_list_scoped_and_redacted(schema):
    session = _session(schema)
    _seed(session)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'editor'})}"}
    # seed an audit row correlated to the agent (lineage) and a turn
    import hashlib as _hl
    session.execute(text(
        "INSERT INTO governance_audit_logs (id, security_domain_id, partition_key, sequence, "
        "actor_user_id, operation, decision, outcome, lineage, event_hash, occurred_at) "
        "VALUES (:id, :dom, 'agent', 1, 'u-1', 'agent.turn.create', 'allow', 'succeeded', "
        "CAST(:lineage AS jsonb), :hash, now())"
    ), {"id": str(uuid.uuid4()), "dom": DEFAULT_DOMAIN,
        "lineage": '{"agent_id": "a-1"}',
        "hash": bytes.fromhex(_hl.sha256(b"audit").hexdigest())})
    session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) VALUES ('s-2','a-1','u-1','active')"
    ))
    session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
        "VALUES ('t-1', 's-2', 'queued', now(), now())"
    ))
    session.commit()
    client = next(_client(session))
    try:
        with TestClient(client) as c:
            r = c.get("/api/v1/agents/a-1/audit", headers=headers)
            assert r.status_code == 200
            items = r.json()["data"]["items"]
            assert len(items) == 1
            assert items[0]["operation"] == "agent.turn.create"
            assert "lineage" not in items[0]  # redacted envelope
    finally:
        session.close()
