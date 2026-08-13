"""P1A-AUDIT: append-only governance audit authority.

Covers the append-only `GovernanceAuditLog`, transaction-owned
`GovernanceAuditOutbox`, `GovernanceAuditChainHead`, the (security_domain,
UTC date) partition chain materialization with `FOR UPDATE` head locking, the
redacted input/output hashing contract, and the migration helper consumed by
revision 0003.

PostgreSQL-marked tests use TEST_DATABASE_URL; SQLite never substitutes.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError


BACKEND_DIR = Path(__file__).resolve().parents[2]
MODEL = BACKEND_DIR / "app" / "models" / "governance_audit.py"
SERVICE = BACKEND_DIR / "app" / "services" / "governance_audit.py"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
DEFAULT_DOMAIN = "00000000-0000-0000-0000-000000000001"


def test_p1a_audit_red_contract():
    missing = [path for path in (MODEL, SERVICE) if not path.exists()]
    if missing:
        pytest.fail(
            "RED_P1A_AUDIT: governance audit foundation missing: "
            + ", ".join(str(path.relative_to(BACKEND_DIR)) for path in missing)
        )


def _scoped_url(schema):
    from urllib.parse import quote

    return f"{TEST_DATABASE_URL}?options={quote(f'-csearch_path={schema}', safe='-=')}"


@pytest.fixture
def audit_schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL required")
    schema = "p1a_audit_" + uuid.uuid4().hex
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _audit_engine(schema):
    return create_engine(_scoped_url(schema))


def _run_helper(engine, helper_name):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.services import governance_audit

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        governance_audit.op = Operations(context)
        getattr(governance_audit, helper_name)()


# ── DB-free service contract ─────────────────────────────────────────────────

def test_partition_key_is_domain_plus_utc_date_and_canonical_event_is_deterministic():
    from app.services.governance_audit import canonical_event, partition_key_for

    occurred = datetime(2026, 8, 13, 12, 0, 0, 123456, tzinfo=timezone.utc)
    assert partition_key_for(DEFAULT_DOMAIN, occurred) == f"{DEFAULT_DOMAIN}:2026-08-13"
    assert partition_key_for(DEFAULT_DOMAIN, datetime(2026, 8, 13, 0, 0, 1, tzinfo=timezone.utc)) == f"{DEFAULT_DOMAIN}:2026-08-13"

    first = canonical_event({"b": 2, "a": 1, "nested": {"z": True, "y": None}})
    second = canonical_event({"nested": {"y": None, "z": True}, "a": 1, "b": 2})
    assert first == second
    assert json.loads(first.decode()) == {"a": 1, "b": 2, "nested": {"y": None, "z": True}}
    assert b" " not in first and b"\n" not in first
    # datetimes serialize exactly as six-fraction-digit UTC Z per the plan
    assert canonical_event({"t": datetime(2026, 8, 13, 1, 2, 3, 4, tzinfo=timezone.utc)}) == b'{"t":"2026-08-13T01:02:03.000004Z"}'
    for rejected in (float("nan"), 1.5, b"bytes", {1, 2}, "\ud800"):
        with pytest.raises(ValueError):
            canonical_event({"v": rejected})


def test_redaction_hashes_payloads_and_never_stores_plaintext():
    from app.services.governance_audit import canonical_event, hash_payload

    assert hash_payload(None) is None
    payload = {"supplier": "机密数据", "amount": 100}
    digest = hash_payload(payload)
    assert digest == hashlib.sha256(canonical_event(payload)).digest()
    assert b"\xe6" not in digest and b"amount" not in digest
    assert hash_payload({"a": 1}) == hash_payload({"a": 1})
    assert hash_payload({"a": 1}) != hash_payload({"a": 2})


class _FakeOp:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return record


def test_audit_migration_helper_upgrade_creates_tables_then_trigger(monkeypatch):
    from app.services import governance_audit

    fake = _FakeOp()
    monkeypatch.setattr(governance_audit, "op", fake)
    governance_audit.upgrade_audit_foundation()
    names = [name for name, _, _ in fake.calls]
    creates = [i for i, name in enumerate(names) if name == "create_table"]
    assert len(creates) == 3
    tables = {fake.calls[i][1][0] for i in creates}
    assert tables == {"governance_audit_logs", "governance_audit_outbox", "governance_audit_chain_heads"}
    trigger_sql = "\n".join(str(args[0]) for name, args, _ in fake.calls if name == "execute")
    assert "reject_governance_audit_log_mutation" in trigger_sql
    assert "GOVERNANCE_AUDIT_APPEND_ONLY" in trigger_sql
    assert trigger_sql.index("CREATE TRIGGER governance_audit_logs_append_only") > trigger_sql.index("CREATE FUNCTION reject_governance_audit_log_mutation")

    fake.calls.clear()
    governance_audit.downgrade_audit_foundation()
    names = [name for name, _, _ in fake.calls]
    drop_table_calls = [i for i, name in enumerate(names) if name == "drop_table"]
    assert len(drop_table_calls) == 3
    assert names.index("execute") < min(drop_table_calls)


def test_append_audit_event_dict_is_canonical_and_chain_hashes_deterministic():
    from app.services.governance_audit import (
        append_audit_event, canonical_event, event_hash,
    )

    occurred = datetime(2026, 8, 13, 1, 2, 3, 4, tzinfo=timezone.utc)
    event = append_audit_event(
        security_domain_id=DEFAULT_DOMAIN,
        operation="ontology.publish",
        decision="allow",
        outcome="succeeded",
        correlation_id="corr-1",
        actor_user_id="user-1",
        policy_ids={"p1": 1},
        lineage={"release_id": "rel-1"},
        input_hash=b"\xab" * 32,
        output_hash=None,
        partition_key=f"{DEFAULT_DOMAIN}:2026-08-13",
        sequence=1,
        previous_hash=None,
        agent_id=None,
        agent_version_id=None,
        release_id="rel-1",
        model_version_id=None,
        connection_version_id=None,
        retention_class="standard",
        occurred_at=occurred,
    )
    assert event["sequence"] == 1
    assert event["previous_hash"] is None
    assert event["occurred_at"] == "2026-08-13T01:02:03.000004Z"
    assert event["input_hash"] == "ab" * 32
    assert event["output_hash"] is None
    assert event["lineage"] == {"release_id": "rel-1"}
    assert "input_payload" not in event and "secret" not in event
    assert event_hash(event) == hashlib.sha256(canonical_event(event)).digest()
    assert event_hash(event) == event_hash(event)
    reordered = dict(reversed(list(event.items())))
    assert event_hash(reordered) == event_hash(event)


# ── ORM storage contract (subprocess keeps the shared metadata SQLite-clean) ─

def test_zzz_audit_orm_exact_storage_and_constraint_contract():
    script = """
import json
from app.models.governance_audit import (
    GovernanceAuditLog, GovernanceAuditOutbox, GovernanceAuditChainHead,
)
print(json.dumps({
    'log': list(GovernanceAuditLog.__table__.c.keys()),
    'log_constraints': [c.name for c in GovernanceAuditLog.__table__.constraints],
    'outbox': list(GovernanceAuditOutbox.__table__.c.keys()),
    'head': list(GovernanceAuditChainHead.__table__.c.keys()),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND_DIR, capture_output=True, text=True, check=True
    )
    metadata = json.loads(result.stdout)
    assert set(metadata["log"]) == {
        "id", "security_domain_id", "partition_key", "sequence", "actor_user_id",
        "operation", "decision", "policy_ids", "correlation_id", "input_hash",
        "output_hash", "lineage", "outcome", "previous_hash", "event_hash",
        "agent_id", "agent_version_id", "release_id", "model_version_id",
        "connection_version_id", "retention_class", "occurred_at",
    }
    assert set(metadata["log_constraints"]) >= {
        "ck_governance_audit_logs_id_uuid",
        "ck_governance_audit_logs_sequence",
        "ck_governance_audit_logs_event_hash",
        "uq_governance_audit_logs_partition_sequence",
    }
    assert set(metadata["outbox"]) == {
        "id", "security_domain_id", "correlation_id", "payload", "state",
        "attempts", "created_at", "updated_at", "materialized_at",
    }
    assert set(metadata["head"]) == {
        "partition_key", "security_domain_id", "next_sequence", "last_hash", "updated_at",
    }
    assert not {"payload", "input", "output", "trace", "secret", "bearer"} & set(metadata["log"])


# ── PostgreSQL append-only chain and outbox fixtures ─────────────────────────

def test_zzzz_postgresql_chain_append_mutation_denial_and_verify(audit_schema):
    from app.services.governance_audit import (
        append_audit, canonical_event, event_hash, verify_chain,
    )

    engine = _audit_engine(audit_schema)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE security_domains (id varchar(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE users (id varchar PRIMARY KEY, security_domain_id varchar(36) NOT NULL)"))
        connection.execute(text("CREATE UNIQUE INDEX uq_users_id_security_domain ON users(id, security_domain_id)"))
        connection.execute(text("INSERT INTO security_domains VALUES (:id)"), {"id": DEFAULT_DOMAIN})
        connection.execute(text("INSERT INTO users VALUES ('creator', :domain)"), {"domain": DEFAULT_DOMAIN})
    _run_helper(engine, "upgrade_audit_foundation")

    occurred = datetime(2026, 8, 13, 1, 2, 3, 4, tzinfo=timezone.utc)
    with engine.begin() as connection:
        first = append_audit(
            connection,
            security_domain_id=DEFAULT_DOMAIN,
            operation="ontology.publish",
            decision="allow",
            outcome="succeeded",
            correlation_id="corr-1",
            actor_user_id="creator",
            policy_ids={"release": "rel-1"},
            input_payload={"changelog": "release one"},
            output_payload=None,
            lineage={"release_id": "rel-1"},
            release_id="rel-1",
            retention_class="standard",
            occurred_at=occurred,
        )
        second = append_audit(
            connection,
            security_domain_id=DEFAULT_DOMAIN,
            operation="auth.login",
            decision="deny",
            outcome="failed",
            correlation_id="corr-2",
            actor_user_id="creator",
            occurred_at=occurred.replace(hour=2),
        )

    assert first["partition_key"] == f"{DEFAULT_DOMAIN}:2026-08-13"
    assert (first["sequence"], second["sequence"]) == (1, 2)
    assert first["previous_hash"] is None
    assert second["previous_hash"] == first["event_hash"]
    # redacted: the log never stores the plaintext payload
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT operation, decision, outcome, input_hash, event_hash, previous_hash, partition_key, sequence "
            "FROM governance_audit_logs ORDER BY sequence"
        )).mappings().all()
        assert [row["operation"] for row in rows] == ["ontology.publish", "auth.login"]
        assert bytes(rows[0]["input_hash"]) == first["input_hash"]
        assert "changelog" not in str(rows[0]["input_hash"])
        assert bytes(rows[1]["previous_hash"]) == bytes(rows[0]["event_hash"])
        assert bytes(rows[0]["event_hash"]) == first["event_hash"]
        head = connection.execute(text(
            "SELECT partition_key, next_sequence, last_hash FROM governance_audit_chain_heads"
        )).mappings().one()
        assert head["next_sequence"] == 3
        assert bytes(head["last_hash"]) == second["event_hash"]

    report = verify_chain(engine, DEFAULT_DOMAIN, occurred_at=occurred)
    assert report["ok"] is True and report["count"] == 2 and report["last_hash"] == second["event_hash"]

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE governance_audit_logs DISABLE TRIGGER governance_audit_logs_append_only"))
        connection.execute(text(
            "UPDATE governance_audit_logs SET event_hash=:h WHERE sequence=1"
        ), {"h": b"z" * 32})
        connection.execute(text("ALTER TABLE governance_audit_logs ENABLE TRIGGER governance_audit_logs_append_only"))
    report = verify_chain(engine, DEFAULT_DOMAIN, occurred_at=occurred)
    assert report["ok"] is False and report["tampered"] >= 1

    with engine.begin() as connection:
        for statement, message in (
            ("UPDATE governance_audit_logs SET outcome='changed' WHERE sequence=1", "GOVERNANCE_AUDIT_APPEND_ONLY"),
            ("DELETE FROM governance_audit_logs WHERE sequence=1", "GOVERNANCE_AUDIT_APPEND_ONLY"),
        ):
            savepoint = connection.begin_nested()
            with pytest.raises(DBAPIError, match=message):
                connection.execute(text(statement))
            savepoint.rollback()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM governance_audit_logs")).scalar_one() == 2

    _run_helper(engine, "downgrade_audit_foundation")
    inspector = inspect(engine)
    assert not {"governance_audit_logs", "governance_audit_outbox", "governance_audit_chain_heads"} & set(inspector.get_table_names())
    engine.dispose()


def test_zzzz_postgresql_outbox_materialization_is_idempotent_and_unique(audit_schema):
    from app.services.governance_audit import (
        append_audit, enqueue_audit, materialize_outbox,
    )

    engine = _audit_engine(audit_schema)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE security_domains (id varchar(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE users (id varchar PRIMARY KEY, security_domain_id varchar(36) NOT NULL)"))
        connection.execute(text("CREATE UNIQUE INDEX uq_users_id_security_domain ON users(id, security_domain_id)"))
        connection.execute(text("INSERT INTO security_domains VALUES (:id)"), {"id": DEFAULT_DOMAIN})
        connection.execute(text("INSERT INTO users VALUES ('creator', :domain)"), {"domain": DEFAULT_DOMAIN})
    _run_helper(engine, "upgrade_audit_foundation")

    payload = {
        "security_domain_id": DEFAULT_DOMAIN,
        "operation": "agent.turn.create",
        "decision": "allow",
        "outcome": "queued",
        "correlation_id": "corr-3",
        "actor_user_id": "creator",
        "retention_class": "standard",
    }
    with engine.begin() as connection:
        outbox_id = enqueue_audit(connection, **payload)
        duplicate = None
        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError):
            duplicate = enqueue_audit(connection, **payload)
        savepoint.rollback()
        assert duplicate is None
        receipt = materialize_outbox(connection, correlation_id="corr-3")

    assert receipt["sequence"] == 1
    with engine.begin() as connection:
        replay = materialize_outbox(connection, correlation_id="corr-3")
    assert replay["already_materialized"] is True
    with engine.connect() as connection:
        state = connection.execute(text(
            "SELECT state, materialized_at IS NOT NULL AS materialized FROM governance_audit_outbox WHERE id=:id"
        ), {"id": outbox_id}).mappings().one()
        assert (state["state"], state["materialized"]) == ("materialized", True)
        assert connection.execute(text(
            "SELECT count(*) FROM governance_audit_logs WHERE correlation_id='corr-3'"
        )).scalar_one() == 1
        # direct append after outbox continues the chain at sequence 2
        connection.execute(text("INSERT INTO governance_audit_logs (id,security_domain_id,partition_key,sequence,actor_user_id,operation,decision,policy_ids,lineage,outcome,event_hash,retention_class) "
                                "VALUES (:id,:domain,:partition,2,'creator','direct','deny','{}','{}','blocked',:hash,'standard')"), {
            "id": str(uuid.uuid4()),
            "domain": DEFAULT_DOMAIN,
            "partition": f"{DEFAULT_DOMAIN}:2026-08-13",
            "hash": b"a" * 32,
        })
    engine.dispose()
