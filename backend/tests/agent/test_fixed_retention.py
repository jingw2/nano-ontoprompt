"""P3A-RETENTION: memory-free fixed-policy purge.

The ten idempotent steps run under a fenced purge job (lease/cursor/
generation) with a purge marker the checkpoint saver's adelete_thread
requires.  Each step is independently durable: crash mid-step resumes from
the cursor and a step never re-runs a known effect.  No memory/dynamic
governance reference.
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


def test_p3a_retention_red_contract():
    failures = []
    for path in ("app/services/retention/fixed_policy.py", "app/tasks/agent_retention.py"):
        p = BACKEND_DIR / path
        if not p.exists():
            failures.append(f"missing {path}")
    svc = BACKEND_DIR / "app" / "services" / "retention" / "fixed_policy.py"
    if svc.exists():
        for symbol in ("run_fixed_purge", "claim_purge_job", "TEN_STEPS"):
            if symbol not in svc.read_text():
                failures.append(f"fixed_policy.py missing {symbol}")
    if failures:
        pytest.fail("RED_P3A_RETENTION: " + "; ".join(failures))


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
    schema = "p3a_retention_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0011_retention_governance").returncode == 0
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _session(schema):
    return sessionmaker(bind=create_engine(_scoped_url(schema)))()


def _seed_turn(session, turn_id="t-old", status="succeeded", age_days=10):
    session.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','s','s@t.com','h','editor',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO ontology_projects (id,name,domain,version,status,created_by,created_at,updated_at,security_domain_id,working_revision) "
        "VALUES ('o-1','O','test','v1','created','u-1',now(),now(),:d,1)"
    ), {"d": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('a-1','private','active','u-1',now(),now())"
    ))
    session.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status) "
        "VALUES ('s-1', 'a-1', 'u-1', 'closed')"
    ))
    old = datetime.now(timezone.utc) - timedelta(days=age_days)
    session.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, created_at, updated_at) "
        "VALUES (:id, 's-1', :status, :old, :old)"
    ), {"id": turn_id, "status": status, "old": old})
    session.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES (:id, 's-1', :turn, 'user', 1, 'hello', :old)"
    ), {"id": str(uuid.uuid4()), "turn": turn_id, "old": old})
    outbox_old = datetime.now(timezone.utc) - timedelta(days=age_days + 31)
    session.execute(text(
        "INSERT INTO agent_turn_dispatch_outbox (id, turn_id, dispatch_generation, operation, state, resolved_at, created_at) "
        "VALUES (:id, :turn, 1, 'turn', 'resolved_terminal', :old, :old)"
    ), {"id": str(uuid.uuid4()), "turn": turn_id, "old": outbox_old})
    session.execute(text(
        "INSERT INTO agent_stream_tickets (id, turn_id, actor_user_id, token_hash, status, expires_at, created_at) "
        "VALUES (:id, :turn, 'u-1', 'h', 'unused', :old, :old)"
    ), {"id": str(uuid.uuid4()), "turn": turn_id, "old": old})
    session.execute(text(
        "INSERT INTO agent_clarification_requests (id, turn_id, question, answer, status, answered_at, created_at) "
        "VALUES (:id, :turn, 'Q', 'A', 'answered', :old, :old)"
    ), {"id": str(uuid.uuid4()), "turn": turn_id, "old": old})
    session.execute(text(
        "INSERT INTO agent_index_outbox (id, ontology_id, event_type, state, created_at) "
        "VALUES (:id, 'o-1', 'upsert_instance', 'applied', :old)"
    ), {"id": str(uuid.uuid4()), "old": old})
    session.commit()


def test_claim_purge_job_single_worker(schema):
    session = _session(schema)
    session.execute(text(
        "INSERT INTO security_domains (id, key, status, created_at) VALUES (:id,'default','active',now()) ON CONFLICT DO NOTHING"
    ), {"id": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO agent_purge_jobs (id, security_domain_id, purge_class, cursor_time, batch_size, generation) "
        "VALUES ('j-1', :dom, 'turn', now(), 500, 0)"
    ), {"dom": DEFAULT_DOMAIN})
    session.commit()
    from app.services.retention.fixed_policy import claim_purge_job
    claim = claim_purge_job(session, security_domain_id=DEFAULT_DOMAIN, purge_class="turn")
    assert claim is not None
    assert claim["generation"] == 1
    # second claim refused while lease live
    second = claim_purge_job(session, security_domain_id=DEFAULT_DOMAIN, purge_class="turn")
    assert second is None
    session.close()


def test_fixed_purge_ten_steps_and_marker(schema):
    session = _session(schema)
    _seed_turn(session)
    session.execute(text(
        "INSERT INTO security_domains (id, key, status, created_at) VALUES (:id,'default','active',now()) ON CONFLICT DO NOTHING"
    ), {"id": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO agent_purge_jobs (id, security_domain_id, purge_class, cursor_time, batch_size, generation) "
        "VALUES ('j-1', :dom, 'turn', now(), 500, 0)"
    ), {"dom": DEFAULT_DOMAIN})
    session.commit()
    from app.services.retention.fixed_policy import claim_purge_job, run_fixed_purge, TEN_STEPS
    claim = claim_purge_job(session, security_domain_id=DEFAULT_DOMAIN, purge_class="turn")
    result = run_fixed_purge(session, security_domain_id=DEFAULT_DOMAIN,
                             job_id=claim["id"], claim_token=claim["claim_token"])
    assert set(result["ledger"]) == set(TEN_STEPS)
    # terminal turn + its messages purged; marker was created then cleaned
    assert session.execute(text("SELECT count(*) FROM agent_turns")).scalar_one() == 0
    assert session.execute(text("SELECT count(*) FROM agent_messages")).scalar_one() == 0
    assert session.execute(text("SELECT count(*) FROM agent_purge_markers")).scalar_one() == 0
    # delivered outbox + stream tickets + clarifications cleaned
    assert session.execute(text("SELECT count(*) FROM agent_turn_dispatch_outbox")).scalar_one() == 0
    assert session.execute(text("SELECT count(*) FROM agent_stream_tickets")).scalar_one() == 0
    assert session.execute(text("SELECT count(*) FROM agent_clarification_requests")).scalar_one() == 0
    # applied index outbox cleaned
    assert session.execute(text("SELECT count(*) FROM agent_index_outbox")).scalar_one() == 0
    session.close()


def test_fence_lost_raises(schema):
    session = _session(schema)
    session.execute(text(
        "INSERT INTO security_domains (id, key, status, created_at) VALUES (:id,'default','active',now()) ON CONFLICT DO NOTHING"
    ), {"id": DEFAULT_DOMAIN})
    session.execute(text(
        "INSERT INTO agent_purge_jobs (id, security_domain_id, purge_class, cursor_time, batch_size, generation, lease_expires_at) "
        "VALUES ('j-1', :dom, 'turn', now(), 500, 0, :old)"
    ), {"dom": DEFAULT_DOMAIN, "old": datetime.now(timezone.utc) - timedelta(seconds=5)})
    session.commit()
    from app.services.retention.fixed_policy import claim_purge_job, run_fixed_purge, RetentionError
    claim = claim_purge_job(session, security_domain_id=DEFAULT_DOMAIN, purge_class="turn")
    # steal the fence with a wrong token -> raises
    with pytest.raises(RetentionError):
        run_fixed_purge(session, security_domain_id=DEFAULT_DOMAIN,
                        job_id=claim["id"], claim_token="forged")
    session.close()


def test_purge_honors_extended_policy_duration(schema):
    """Activating a longer policy for message.redact means a message younger
    than the new (longer) duration is NOT redacted, even though it's older
    than the old 90-day minimum would have required... this test uses a
    message exactly between the two durations to prove the active policy,
    not the hardcoded 90 days, drives the decision."""
    from app.services.retention.policy import activate_policy_version, create_policy_version, current_epoch
    from app.services.retention.fixed_policy import claim_purge_job, run_fixed_purge

    session = _session(schema)
    _seed_turn(session, turn_id="t-1", age_days=0)  # recent turn: survives step 8 so its messages aren't bulk-deleted

    old_enough_for_90_not_180 = datetime.now(timezone.utc) - timedelta(days=120)
    session.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES ('m-extend', 's-1', 't-1', 'assistant', 9, 'still here', :ts)"
    ), {"ts": old_enough_for_90_not_180})
    session.execute(text(
        "INSERT INTO agent_purge_jobs (id, security_domain_id, purge_class, cursor_time, batch_size, generation) "
        "VALUES ('j-1', :dom, 'turn', now(), 500, 0)"
    ), {"dom": DEFAULT_DOMAIN})
    session.commit()

    version = create_policy_version(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
                                    rules={"message.redact": 180})
    activate_policy_version(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
                            version_id=version["id"], base_epoch=current_epoch(session, DEFAULT_DOMAIN))

    claim = claim_purge_job(session, security_domain_id=DEFAULT_DOMAIN, purge_class="turn")
    run_fixed_purge(session, security_domain_id=DEFAULT_DOMAIN, job_id=claim["id"], claim_token=claim["claim_token"])

    content = session.execute(text(
        "SELECT content FROM agent_messages WHERE id = 'm-extend'"
    )).scalar_one()
    assert content == "still here"  # NOT redacted — 120 days < the newly active 180-day duration
    session.close()


def test_purge_skips_held_turn(schema):
    from app.services.retention.holds import create_hold
    from app.services.retention.fixed_policy import claim_purge_job, run_fixed_purge

    session = _session(schema)
    _seed_turn(session, turn_id="t-1", age_days=30)  # well past the 7-day turn.delete minimum

    create_hold(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
               scope_type="turn", scope_id="t-1", reason="litigation")

    session.execute(text(
        "INSERT INTO agent_purge_jobs (id, security_domain_id, purge_class, cursor_time, batch_size, generation) "
        "VALUES ('j-1', :dom, 'turn', now(), 500, 0)"
    ), {"dom": DEFAULT_DOMAIN})
    session.commit()

    claim = claim_purge_job(session, security_domain_id=DEFAULT_DOMAIN, purge_class="turn")
    run_fixed_purge(session, security_domain_id=DEFAULT_DOMAIN, job_id=claim["id"], claim_token=claim["claim_token"])

    still_there = session.execute(text(
        "SELECT count(*) FROM agent_turns WHERE id = 't-1'"
    )).scalar_one()
    assert still_there == 1  # held — not purged despite being past the 7-day turn.delete minimum
    session.close()


def test_purge_release_then_purge_removes_previously_held_turn(schema):
    from app.services.retention.holds import create_hold, release_hold
    from app.services.retention.fixed_policy import claim_purge_job, run_fixed_purge

    session = _session(schema)
    _seed_turn(session, turn_id="t-1", age_days=30)  # well past the 7-day turn.delete minimum

    hold = create_hold(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
                       scope_type="turn", scope_id="t-1", reason="litigation")

    session.execute(text(
        "INSERT INTO agent_purge_jobs (id, security_domain_id, purge_class, cursor_time, batch_size, generation) "
        "VALUES ('j-1', :dom, 'turn', now(), 500, 0)"
    ), {"dom": DEFAULT_DOMAIN})
    session.commit()

    claim = claim_purge_job(session, security_domain_id=DEFAULT_DOMAIN, purge_class="turn")
    run_fixed_purge(session, security_domain_id=DEFAULT_DOMAIN, job_id=claim["id"], claim_token=claim["claim_token"])
    still_there = session.execute(text("SELECT count(*) FROM agent_turns WHERE id = 't-1'")).scalar_one()
    assert still_there == 1  # held — first purge skips it

    release_hold(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN, hold_id=hold["id"])

    # the first claim's 30s lease is still live at this point in the test;
    # simulate it having expired so the second claim isn't refused by lease
    # fencing (already covered by test_claim_purge_job_single_worker) —
    # this test is about hold release unblocking purge, not lease exclusivity
    session.execute(text("UPDATE agent_purge_jobs SET lease_expires_at = NULL WHERE id = 'j-1'"))
    claim2 = claim_purge_job(session, security_domain_id=DEFAULT_DOMAIN, purge_class="turn")
    run_fixed_purge(session, security_domain_id=DEFAULT_DOMAIN, job_id=claim2["id"], claim_token=claim2["claim_token"])
    gone = session.execute(text("SELECT count(*) FROM agent_turns WHERE id = 't-1'")).scalar_one()
    assert gone == 0  # released — second purge removes it
    session.close()


def test_purge_session_scope_hold_protects_redaction_steps(schema):
    """A session-scope hold must protect the turn.delete-adjacent redaction
    steps (4 runtime events, 4 messages, 5 model invocations), not just the
    step-8 Turn delete — this is the Fix 1 gap the final review flagged as
    Critical."""
    from app.services.retention.holds import create_hold
    from app.services.retention.fixed_policy import claim_purge_job, run_fixed_purge

    session = _session(schema)
    _seed_turn(session, turn_id="t-1", age_days=200)  # past every redaction cutoff

    session.execute(text(
        "INSERT INTO agent_runtime_events (id, turn_id, sequence, event_type, payload, created_at) "
        "VALUES ('re-1', 't-1', 1, 'node_start', '{\"k\": \"v\"}'::json, "
        "now() - interval '200 days')"
    ))
    session.execute(text(
        "INSERT INTO agent_model_invocations (id, turn_id, invocation_slot, idempotency_key, "
        "request_hash, status, created_at) "
        "VALUES ('mi-1', 't-1', 1, 'idem-1', 'hash-1', 'succeeded', now() - interval '200 days')"
    ))
    session.execute(text(
        "INSERT INTO agent_purge_jobs (id, security_domain_id, purge_class, cursor_time, batch_size, generation) "
        "VALUES ('j-1', :dom, 'turn', now(), 500, 0)"
    ), {"dom": DEFAULT_DOMAIN})
    session.commit()

    create_hold(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
               scope_type="session", scope_id="s-1", reason="litigation")

    claim = claim_purge_job(session, security_domain_id=DEFAULT_DOMAIN, purge_class="turn")
    run_fixed_purge(session, security_domain_id=DEFAULT_DOMAIN, job_id=claim["id"], claim_token=claim["claim_token"])

    event_payload = session.execute(text(
        "SELECT payload FROM agent_runtime_events WHERE id = 're-1'"
    )).scalar_one()
    assert event_payload == {"k": "v"}  # NOT redacted — session hold protects it

    message_content = session.execute(text(
        "SELECT content FROM agent_messages WHERE turn_id = 't-1' AND role = 'user'"
    )).scalar_one()
    assert message_content == "hello"  # NOT redacted (from _seed_turn's own insert)

    mi_count = session.execute(text(
        "SELECT count(*) FROM agent_model_invocations WHERE id = 'mi-1'"
    )).scalar_one()
    assert mi_count == 1  # NOT deleted — session hold protects it
    session.close()
