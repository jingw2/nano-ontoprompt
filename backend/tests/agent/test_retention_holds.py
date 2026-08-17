"""P6A: legal holds block purge eligibility for their scoped rows."""
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
    schema = "p6a_holds_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    assert _alembic(schema, "upgrade", "0013_external_tool_alias_unique").returncode == 0
    # migration 0003_publication_governance.py already seeds a default
    # security_domains row (id=DEFAULT_DOMAIN) unconditionally, and 0011's
    # own backfill creates its retention_epochs row — do NOT manually
    # INSERT either here, it would violate their primary keys
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


def test_p6a_holds_red_contract():
    module = BACKEND_DIR / "app" / "services" / "retention" / "holds.py"
    if not module.exists():
        pytest.fail("RED_P6A_HOLDS: missing app/services/retention/holds.py")


def test_create_hold_rejects_invalid_scope(session):
    from app.services.retention.holds import create_hold, RetentionHoldError
    with pytest.raises(RetentionHoldError, match="HOLD_SCOPE_INVALID"):
        create_hold(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
                   scope_type="not_a_scope", scope_id="t-1", reason="litigation")


def test_create_hold_blocks_and_release_unblocks(session):
    from app.services.retention.holds import create_hold, is_held, release_hold
    assert is_held(session, DEFAULT_DOMAIN, "turn", "t-1") is False
    hold = create_hold(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
                       scope_type="turn", scope_id="t-1", reason="litigation hold")
    assert is_held(session, DEFAULT_DOMAIN, "turn", "t-1") is True

    release_hold(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN, hold_id=hold["id"])
    assert is_held(session, DEFAULT_DOMAIN, "turn", "t-1") is False


def test_release_hold_not_found(session):
    from app.services.retention.holds import release_hold, RetentionHoldError
    with pytest.raises(RetentionHoldError, match="HOLD_NOT_FOUND"):
        release_hold(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN, hold_id="missing")


def test_create_hold_bumps_epoch(session):
    from app.services.retention.policy import current_epoch
    from app.services.retention.holds import create_hold
    epoch_before = current_epoch(session, DEFAULT_DOMAIN)
    create_hold(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
               scope_type="session", scope_id="s-1", reason="audit")
    assert current_epoch(session, DEFAULT_DOMAIN) == epoch_before + 1


def test_is_held_true_with_multiple_concurrent_holds_on_same_scope(session):
    """is_held must not crash (MultipleResultsFound) when 2+ holds are
    active on the same scope — nothing in the schema prevents this."""
    from app.services.retention.holds import create_hold, is_held
    create_hold(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
               scope_type="turn", scope_id="t-2", reason="matter A")
    create_hold(session, actor_id="u-1", security_domain_id=DEFAULT_DOMAIN,
               scope_type="turn", scope_id="t-2", reason="matter B")
    assert is_held(session, DEFAULT_DOMAIN, "turn", "t-2") is True
