"""P5C-EXECUTE: governed instance action execution.

Consumed approval -> CAS approved->consumed, execution to executing, locked
effect with worker fence; known outcome once, unknown outcome creates a
reconciliation case (no automatic replay); operator resolution retries with
a new dispatch generation or terminalizes.
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

from app.routers.agent_reconciliations import router
from app.services.auth_service import create_access_token


BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p5c_execute_red_contract():
    failures = []
    for path in ("app/services/actions/execution.py",
                 "app/services/runtime/reconciliation.py",
                 "app/routers/agent_reconciliations.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    svc = BACKEND_DIR / "app" / "services" / "actions" / "execution.py"
    if svc.exists():
        for symbol in ("execute_approved_action", "record_unknown_outcome"):
            if symbol not in svc.read_text():
                failures.append(f"execution.py missing {symbol}")
    if failures:
        pytest.fail("RED_P5C_EXECUTE: " + "; ".join(failures))


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
    schema = "p5c_execute_" + uuid.uuid4().hex
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


def _seed(schema, *, approval_status="approved", turn_status="running", claim_token="tok"):
    s = _session(schema)
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','s','s@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-1','O','test','v1','created','u-1',now(),now(),:d,1)"
    ), {"d": DEFAULT_DOMAIN})
    s.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('a-1','private','active','u-1',now(),now())"
    ))
    s.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) "
        "VALUES ('s-1', 'a-1', 'u-1', 'active')"
    ))
    s.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, claim_token, created_at, updated_at) "
        "VALUES ('t-1', 's-1', :status, :ct, now(), now())"
    ), {"status": turn_status, "ct": claim_token})
    s.execute(text(
        "INSERT INTO agent_tool_executions (id, turn_id, idempotency_key, status, descriptor, "
        "parameters_hash, created_at, updated_at) "
        "VALUES ('te-1', 't-1', 'exec-1', 'proposed', '{}'::json, :ph, now(), now())"
    ), {"ph": "p" * 64})
    s.execute(text(
        "INSERT INTO agent_approvals (id, turn_id, tool_execution_id, preview_hash, parameter_hash, "
        "schema_hash, release_hash, model_hash, policy_hash, designated_actor_id, revision, "
        "expires_at, status, created_at, updated_at) "
        "VALUES ('ap-1', 't-1', 'te-1', :ph, :pah, :sh, :rh, :mh, :oh, 'u-1', 2, "
        "now() + interval '1 hour', :status, now(), now())"
    ), {"ph": "h" * 64, "pah": "p" * 64, "sh": "s" * 64, "rh": "r" * 64,
        "mh": "m" * 64, "oh": "o" * 64, "status": approval_status})
    s.commit()
    s.close()


def test_execute_consumes_and_applies_once(schema):
    _seed(schema)
    s = _session(schema)
    from app.services.actions.execution import execute_approved_action
    result = execute_approved_action(
        s, approval_id="ap-1", worker_claim_token="tok",
        effect_payload={"set": {"status": "resolved"}}, result_hash="z" * 64,
    )
    assert result["outcome"] == "known"
    assert s.execute(text("SELECT status FROM agent_approvals WHERE id = 'ap-1'")).scalar_one() == "consumed"
    assert s.execute(text("SELECT status FROM agent_tool_executions WHERE id = 'te-1'")).scalar_one() == "succeeded"
    assert s.execute(text("SELECT result_hash FROM agent_tool_executions WHERE id = 'te-1'")).scalar_one() == "z" * 64
    s.close()


def test_execute_requires_live_worker_fence(schema):
    _seed(schema)
    s = _session(schema)
    from app.services.actions.execution import execute_approved_action, ExecutionError
    with pytest.raises(ExecutionError, match="TURN_FENCE_LOST"):
        execute_approved_action(s, approval_id="ap-1", worker_claim_token="forged",
                                effect_payload={}, result_hash=None)
    # approval untouched (still approved)
    assert s.execute(text("SELECT status FROM agent_approvals WHERE id = 'ap-1'")).scalar_one() == "approved"
    s.close()


def test_execute_rejects_non_approved(schema):
    _seed(schema, approval_status="rejected")
    s = _session(schema)
    from app.services.actions.execution import execute_approved_action, ExecutionError
    with pytest.raises(ExecutionError, match="APPROVAL_NOT_CONSUMED"):
        execute_approved_action(s, approval_id="ap-1", worker_claim_token="tok",
                                effect_payload={}, result_hash=None)
    s.close()


def test_unknown_outcome_creates_reconciliation_case(schema):
    _seed(schema)
    s = _session(schema)
    from app.services.actions.execution import record_unknown_outcome
    result = record_unknown_outcome(
        s, approval_id="ap-1", turn_id="t-1", execution_kind="model",
        execution_id="inv-1", request_hash="q" * 64,
    )
    assert result["requires_operator"] is True
    assert s.execute(text("SELECT count(*) FROM agent_reconciliation_cases "
                          "WHERE execution_id = 'inv-1'")).scalar_one() == 1
    # no automatic replay: no resume outbox created
    assert s.execute(text("SELECT count(*) FROM agent_turn_dispatch_outbox")).scalar_one() == 0
    s.close()


def test_operator_resolve_retry_resumes_with_new_generation(schema):
    _seed(schema)
    s = _session(schema)
    from app.services.actions.execution import record_unknown_outcome
    record_unknown_outcome(s, approval_id="ap-1", turn_id="t-1", execution_kind="model",
                           execution_id="inv-1", request_hash="q" * 64)
    from app.services.runtime.reconciliation import resolve_case
    result = resolve_case(s, case_id=s.execute(text(
        "SELECT id FROM agent_reconciliation_cases LIMIT 1"
    )).scalar_one(), base_revision=1, resolution="retry", actor_id="u-1")
    assert result["resumed"] is True
    assert s.execute(text(
        "SELECT dispatch_generation FROM agent_turns WHERE id = 't-1'"
    )).scalar_one() == 2
    assert s.execute(text(
        "SELECT count(*) FROM agent_turn_dispatch_outbox WHERE operation = 'delivery_recovery'"
    )).scalar_one() == 1
    s.close()


def test_reconciliation_api_admin_only(schema):
    _seed(schema)
    s = _session(schema)
    from app.services.actions.execution import record_unknown_outcome
    record_unknown_outcome(s, approval_id="ap-1", turn_id="t-1", execution_kind="model",
                           execution_id="inv-1", request_hash="q" * 64)
    case_id = s.execute(text("SELECT id FROM agent_reconciliation_cases LIMIT 1")).scalar_one()
    editor_id = str(uuid.uuid4())
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES (:id,'ed','ed@t.com','h','editor',true,:d,now(),now())"
    ), {"id": editor_id, "d": DEFAULT_DOMAIN})
    s.commit()
    admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'admin'})}"}
    editor_headers = {"Authorization": f"Bearer {create_access_token({'sub': editor_id, 'role': 'editor'})}"}
    client = next(_client(s))
    try:
        with TestClient(client) as c:
            assert c.get("/api/v1/admin/agent-reconciliations", headers=admin_headers).status_code == 200
            assert c.get("/api/v1/admin/agent-reconciliations", headers=editor_headers).status_code == 403
            r = c.post(f"/api/v1/admin/agent-reconciliations/{case_id}/resolve",
                       json={"base_revision": 1, "resolution": "failed"}, headers=admin_headers)
            assert r.status_code == 200
            # double resolve -> 409
            r = c.post(f"/api/v1/admin/agent-reconciliations/{case_id}/resolve",
                       json={"base_revision": 2, "resolution": "retry"}, headers=admin_headers)
            assert r.status_code == 409
    finally:
        s.close()


def _client(session):
    from app.deps import get_db

    def override_get_db():
        yield session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()
