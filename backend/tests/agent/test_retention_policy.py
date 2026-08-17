"""P6A: epoch lock, minimum-floor duration resolution."""
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
    schema = "p6a_policy_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    # Upgrade to the current head (0013); 0011's backfill creates the default
    # domain's policy + epoch, from the security_domain seeded in 0003.
    assert _alembic(schema, "upgrade", "0013_external_tool_alias_unique").returncode == 0
    s = sessionmaker(bind=create_engine(_scoped_url(schema)))()
    s.execute(text(
        "INSERT INTO users (id,username,email,password_hash,role,is_active,security_domain_id,created_at,updated_at) "
        "VALUES ('u-1','a','a@t.com','h','admin',true,:d,now(),now())"
    ), {"d": DEFAULT_DOMAIN})
    s.commit()
    yield s
    s.close()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def test_p6a_policy_red_contract():
    module = BACKEND_DIR / "app" / "services" / "retention" / "policy.py"
    if not module.exists():
        pytest.fail("RED_P6A_POLICY: missing app/services/retention/policy.py")
    source = module.read_text()
    for symbol in ("TABLE_MINIMUMS", "acquire_domain_lock", "bump_epoch", "resolve_active_duration"):
        if symbol not in source:
            pytest.fail(f"RED_P6A_POLICY: policy.py missing {symbol}")


def test_bump_epoch_increments_and_persists(session):
    from app.services.retention.policy import acquire_domain_lock, bump_epoch, current_epoch
    with session.begin():
        acquire_domain_lock(session, DEFAULT_DOMAIN)
        new_epoch = bump_epoch(session, DEFAULT_DOMAIN)
    assert new_epoch == 1
    assert current_epoch(session, DEFAULT_DOMAIN) == 1


def test_resolve_active_duration_uses_backfilled_minimum(session):
    from app.services.retention.policy import resolve_active_duration
    assert resolve_active_duration(session, DEFAULT_DOMAIN, "message.redact") == 90
    assert resolve_active_duration(session, DEFAULT_DOMAIN, "turn.delete") == 7


def test_resolve_active_duration_rejects_unknown_class(session):
    from app.services.retention.policy import resolve_active_duration, RetentionPolicyError
    with pytest.raises(RetentionPolicyError, match="RETENTION_CLASS_UNKNOWN"):
        resolve_active_duration(session, DEFAULT_DOMAIN, "not_a_real_class")


def test_resolve_active_duration_never_goes_below_minimum_even_if_rules_missing_key(session):
    """A policy version whose rules JSON omits a class_action (e.g. an older,
    narrower policy) still floors to TABLE_MINIMUMS, never to zero/missing."""
    from app.services.retention.policy import resolve_active_duration
    policy_id = session.execute(text(
        "SELECT id FROM retention_policies WHERE security_domain_id = :d"
    ), {"d": DEFAULT_DOMAIN}).scalar_one()
    version_id = str(uuid.uuid4())
    session.execute(text(
        "INSERT INTO retention_policy_versions (id, policy_id, version_no, rules, canonical_hash, effective_at, status, created_at) "
        "VALUES (:id, :policy, 2, CAST(:rules AS json), 'x', now(), 'active', now())"
    ), {"id": version_id, "policy": policy_id, "rules": "{}"})
    session.execute(text(
        "UPDATE retention_policies SET active_version_id = :v WHERE id = :p"
    ), {"v": version_id, "p": policy_id})
    session.commit()
    assert resolve_active_duration(session, DEFAULT_DOMAIN, "message.redact") == 90


def test_create_policy_version_rejects_below_minimum(session):
    from app.services.retention.policy import create_policy_version, RetentionPolicyError
    with pytest.raises(RetentionPolicyError, match="RETENTION_MINIMUM_VIOLATION"):
        create_policy_version(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
                              rules={"message.redact": 10})  # below the 90-day floor


def test_create_policy_version_rejects_unknown_class(session):
    from app.services.retention.policy import create_policy_version, RetentionPolicyError
    with pytest.raises(RetentionPolicyError, match="RETENTION_CLASS_UNKNOWN"):
        create_policy_version(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
                              rules={"not_a_real_class": 999})


def test_create_and_activate_policy_version_extends_duration(session):
    from app.services.retention.policy import (
        activate_policy_version, create_policy_version, current_epoch, resolve_active_duration,
    )
    created = create_policy_version(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
                                    rules={"message.redact": 180})
    assert created["version_no"] == 2
    assert created["status"] == "pending"
    epoch_before = current_epoch(session, DEFAULT_DOMAIN)

    activated = activate_policy_version(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
                                        version_id=created["id"], base_epoch=epoch_before)
    assert activated["epoch"] == epoch_before + 1
    assert resolve_active_duration(session, DEFAULT_DOMAIN, "message.redact") == 180
    # a class omitted from the new policy's rules still floors to its minimum
    assert resolve_active_duration(session, DEFAULT_DOMAIN, "turn.delete") == 7


def test_activate_policy_version_stale_epoch_conflicts(session):
    from app.services.retention.policy import (
        activate_policy_version, create_policy_version, RetentionPolicyConflict,
    )
    created = create_policy_version(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
                                    rules={"message.redact": 180})
    with pytest.raises(RetentionPolicyConflict, match="RETENTION_EPOCH_CONFLICT"):
        activate_policy_version(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
                                version_id=created["id"], base_epoch=999)
