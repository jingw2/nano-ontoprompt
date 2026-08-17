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
    # Run migrations up to 0010, which creates security_domains + other base tables
    # Then 0011 will auto-backfill policies for any existing domains
    assert _alembic(schema, "upgrade", "0010_agent_single_binding").returncode == 0
    # Now run 0011 which will create the policy + epoch for the default domain created in 0003
    assert _alembic(schema, "upgrade", "0011_retention_governance").returncode == 0
    s = sessionmaker(bind=create_engine(_scoped_url(schema)))()
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
