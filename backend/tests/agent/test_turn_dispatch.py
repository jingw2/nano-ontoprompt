"""P3A-DISPATCH: durable Agent Turn dispatch.

Transactional outbox publisher (FOR UPDATE SKIP LOCKED), single CAS claim,
heartbeat lease extension, delivered-deadline watchdog with generation CAS and
recovery outbox, expired-lease sweeper, and pre/post-claim cancel.  Evidence:
race/loss/ancestor-resolution ledger; exactly one authoritative claim.
"""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p3a_dispatch_red_contract():
    failures = []
    for path in ("app/services/runtime/dispatch.py", "app/tasks/agent_dispatch.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    dispatch = BACKEND_DIR / "app" / "services" / "runtime" / "dispatch.py"
    if dispatch.exists():
        for symbol in ("publish_pending_dispatch", "claim_turn", "heartbeat_turn",
                       "watchdog_recover_delivered", "sweep_expired_leases", "cancel_turn"):
            if symbol not in dispatch.read_text():
                failures.append(f"dispatch.py missing {symbol}")
    if failures:
        pytest.fail("RED_P3A_DISPATCH: " + "; ".join(failures))


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
    schema = "p3a_dispatch_" + uuid.uuid4().hex
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


def _seed(connection, *, turn_id="t-1", session_id="s-1", agent_id="a-1"):
    connection.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','s','s@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    connection.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-1','O','test','v1','created','u-1',now(),now(),:d,1)"
    ), {"d": DEFAULT_DOMAIN})
    connection.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES (:id,'private','active','u-1',now(),now())"
    ), {"id": agent_id})
    connection.execute(text(
        "INSERT INTO agent_sessions (id,agent_id,owner_user_id,status) "
        "VALUES (:sid,:aid,'u-1','active')"
    ), {"sid": session_id, "aid": agent_id})
    connection.execute(text(
        "INSERT INTO agent_turns (id,session_id,status) VALUES (:tid,:sid,'queued')"
    ), {"tid": turn_id, "sid": session_id})


def _queue(connection, turn_id="t-1", generation=1, operation="turn"):
    connection.execute(text(
        "INSERT INTO agent_turn_dispatch_outbox (id,turn_id,dispatch_generation,operation,state) "
        "VALUES (:id,:tid,:gen,:op,'pending')"
    ), {"id": str(uuid.uuid4()), "tid": turn_id, "gen": generation, "op": operation})


def _status(connection, turn_id="t-1"):
    return connection.execute(text(
        "SELECT status, dispatch_generation, claim_generation, claim_token, lease_expires_at "
        "FROM agent_turns WHERE id = :id"
    ), {"id": turn_id}).mappings().one()


def _outbox_states(connection, turn_id="t-1"):
    return connection.execute(text(
        "SELECT dispatch_generation, operation, state, resolution "
        "FROM agent_turn_dispatch_outbox WHERE turn_id = :id ORDER BY dispatch_generation, created_at"
    ), {"id": turn_id}).mappings().all()


def test_publisher_marks_delivered_with_deadline(schema):
    session = _session(schema)
    _seed(session)
    _queue(session)
    session.commit()
    from app.services.runtime.dispatch import publish_pending_dispatch
    published = publish_pending_dispatch(session, publish=lambda oid, tid, op: f"broker-{oid}")
    assert len(published) == 1
    row = session.execute(text(
        "SELECT state, published_at, broker_message_id, claim_deadline_at "
        "FROM agent_turn_dispatch_outbox"
    )).mappings().one()
    assert row["state"] == "delivered"
    assert row["broker_message_id"].startswith("broker-")
    assert row["claim_deadline_at"] > row["published_at"]
    session.close()


def test_single_cas_claim_one_authoritative(schema):
    """Two racing claims: exactly one wins; the loser raises DISPATCH_CLAIM_STALE."""
    session = _session(schema)
    _seed(session)
    _queue(session)
    session.commit()
    from app.services.runtime.dispatch import claim_turn, DispatchError
    first = claim_turn(session, turn_id="t-1", dispatch_generation=1,
                       worker_artifact_id="w-1", claim_token="tok-a")
    assert first["claim_generation"] == 1
    with pytest.raises(DispatchError) as excinfo:
        claim_turn(session, turn_id="t-1", dispatch_generation=1,
                   worker_artifact_id="w-1", claim_token="tok-b")
    assert "DISPATCH_CLAIM_STALE" in str(excinfo.value)
    row = _status(session)
    assert row["status"] == "running"
    assert row["claim_token"] == "tok-a"
    session.close()


def test_claim_requires_exact_generation_and_no_cancel(schema):
    session = _session(schema)
    _seed(session)
    _queue(session, generation=1)
    session.commit()
    from app.services.runtime.dispatch import claim_turn, DispatchError, cancel_turn
    with pytest.raises(DispatchError):
        claim_turn(session, turn_id="t-1", dispatch_generation=2,
                   worker_artifact_id="w-1", claim_token="tok")
    cancel_turn(session, turn_id="t-1", actor_id="u-1")
    with pytest.raises(DispatchError):
        claim_turn(session, turn_id="t-1", dispatch_generation=1,
                   worker_artifact_id="w-1", claim_token="tok")
    session.close()


def test_heartbeat_extends_lease_and_fence_lost_raises(schema):
    session = _session(schema)
    _seed(session)
    _queue(session)
    session.commit()
    from app.services.runtime.dispatch import claim_turn, heartbeat_turn, DispatchError
    claim_turn(session, turn_id="t-1", dispatch_generation=1,
               worker_artifact_id="w-1", claim_token="tok")
    now = datetime.now(timezone.utc)
    beat = heartbeat_turn(session, turn_id="t-1", claim_token="tok")
    assert beat["lease_expires_at"] > now + timedelta(seconds=25)
    with pytest.raises(DispatchError):
        heartbeat_turn(session, turn_id="t-1", claim_token="wrong")
    session.close()


def test_watchdog_cas_generation_and_recovery_outbox(schema):
    """Delivered-but-unclaimed past deadline: generation CAS + recovery outbox
    (ancestor-resolution ledger: old row delivered_unclaimed, new row pending)."""
    session = _session(schema)
    _seed(session)
    _queue(session)
    session.commit()
    from app.services.runtime.dispatch import publish_pending_dispatch, watchdog_recover_delivered
    publish_pending_dispatch(session)
    session.execute(text(
        "UPDATE agent_turn_dispatch_outbox SET claim_deadline_at = :old "
        "WHERE state = 'delivered'"
    ), {"old": datetime.now(timezone.utc) - timedelta(seconds=60)})
    session.commit()
    recovered = watchdog_recover_delivered(session)
    assert len(recovered) == 1
    assert recovered[0]["new_generation"] == 2
    rows = _outbox_states(session)
    assert {r["operation"] for r in rows} == {"turn", "delivery_recovery"}
    turn = _status(session)
    assert turn["dispatch_generation"] == 2
    assert turn["status"] == "queued"
    session.close()


def test_sweeper_releases_expired_lease_to_queued(schema):
    session = _session(schema)
    _seed(session)
    _queue(session)
    session.commit()
    from app.services.runtime.dispatch import claim_turn, sweep_expired_leases
    claim_turn(session, turn_id="t-1", dispatch_generation=1,
               worker_artifact_id="w-1", claim_token="tok")
    session.execute(text(
        "UPDATE agent_turns SET lease_expires_at = :old WHERE id = 't-1'"
    ), {"old": datetime.now(timezone.utc) - timedelta(seconds=5)})
    session.commit()
    swept = sweep_expired_leases(session)
    assert len(swept) == 1
    turn = _status(session)
    assert turn["status"] == "queued"
    assert turn["dispatch_generation"] == 2
    assert turn["claim_token"] is None
    session.close()


def test_preclaim_cancel_terminalizes_and_resolves_outbox(schema):
    session = _session(schema)
    _seed(session)
    _queue(session)
    session.commit()
    from app.services.runtime.dispatch import publish_pending_dispatch, cancel_turn
    publish_pending_dispatch(session)
    result = cancel_turn(session, turn_id="t-1", actor_id="u-1")
    assert result["status"] == "cancelled"
    turn = _status(session)
    assert turn["status"] == "cancelled"
    rows = _outbox_states(session)
    assert all(r["resolution"] == "cancelled" for r in rows)
    session_active = session.execute(text(
        "SELECT active_turn_id FROM agent_sessions WHERE id = 's-1'"
    )).scalar_one()
    assert session_active is None
    session.close()


def test_postclaim_cancel_signals_cancelling(schema):
    session = _session(schema)
    _seed(session)
    _queue(session)
    session.commit()
    from app.services.runtime.dispatch import claim_turn, cancel_turn
    claim_turn(session, turn_id="t-1", dispatch_generation=1,
               worker_artifact_id="w-1", claim_token="tok")
    result = cancel_turn(session, turn_id="t-1", actor_id="u-1")
    assert result["status"] == "cancelling"
    assert _status(session)["status"] == "cancelling"
    session.close()


def test_ancestor_resolution_marks_superseded_on_claim(schema):
    """An older pending generation is resolved_superseded when a newer
    generation claims the turn."""
    session = _session(schema)
    _seed(session)
    _queue(session, generation=1)
    _queue(session, generation=2)
    session.execute(text("UPDATE agent_turns SET dispatch_generation = 2 WHERE id = 't-1'"))
    session.commit()
    from app.services.runtime.dispatch import claim_turn, publish_pending_dispatch
    publish_pending_dispatch(session)  # both generations -> delivered
    claim_turn(session, turn_id="t-1", dispatch_generation=2,
               worker_artifact_id="w-1", claim_token="tok")
    rows = _outbox_states(session)
    by_gen = {r["dispatch_generation"]: r for r in rows}
    assert by_gen[1]["resolution"] == "superseded"
    assert by_gen[2]["state"] == "claimed"
    session.rollback()
    session.close()
