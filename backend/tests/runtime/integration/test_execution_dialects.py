"""Integration tests for `execute_plan` (Task 26) against real, disposable
PostgreSQL and MySQL databases (`test_data/runtime/db/docker-compose.yml`).

`tests/runtime/test_execution_service.py` already proves every preflight
governance boundary, the reconciliation/UNKNOWN path, and rollback using a
SQLite unit harness with `_resolve_connection_url`/`_fetch_expected_version`/
`_writer_for_dialect` mocked out (`_FakeWriter`) — none of that ever opens a
real connector. This suite is the complement: it never mocks any of those
three functions, so `execute_plan` really does open `psycopg2`/`pymysql`
connections, read a live `row_version`, and call the real
`PostgresRowWriter`/`MySQLRowWriter` against the same disposable
`managed_targets` fixture table Tasks 24/25's own writer integration suites
(`test_postgres_writer_integration.py`/`test_mysql_writer_integration.py`)
already use.

Governance rows (`RuntimePlan`/`ManagedActionBinding`/`SandboxSimulation`/...)
still live in the ORM test harness database (the `db` fixture from
`tests/conftest.py`, SQLite-backed) exactly as in the unit suite — only the
writer *target* (`managed_targets` in the real PostgreSQL/MySQL fixture) and
the version-read this module performs immediately before writing are real.
This mirrors production: `execute_plan` never assumes governance metadata and
the row it writes live in the same database.

`append_audit`/`persist_idempotency` are still faked exactly as
`test_execution_service.py` fakes them, for the same reason: both write to
tables (`governance_audit_*`, `agent_idempotency_keys`) that are genuinely
PostgreSQL-only infrastructure (a `SELECT ... FOR UPDATE` chain head; a table
deliberately excluded from the ORM metadata — see `idempotency.py`'s own
docstring) that the SQLite harness never creates. Faking them changes nothing
about what this suite proves: the real target is the writer/version-read
path, not the (already-proven-correct-by-code-review, Postgres-only)
audit/idempotency ledger tables.

Run:
    docker compose -f test_data/runtime/db/docker-compose.yml up -d --wait
    (cd backend && RUNTIME_POSTGRES_URL=postgresql://runtime:runtime@localhost:55432/runtime \\
        RUNTIME_MYSQL_URL=mysql+pymysql://runtime:runtime@localhost:53306/runtime \\
        python -m pytest tests/runtime/integration/test_execution_dialects.py -q)

Each test skips (rather than fails) when its own dialect's URL env var is not
configured, exactly like `test_{postgres,mysql}_writer_integration.py`.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from sqlalchemy import text

from app.models.action import Action
from app.models.managed_action import ManagedActionBinding
from app.models.oauth import OAuthClient
from app.models.ontology import OntologyProject
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.runtime_plan import RuntimePlan
from app.models.sandbox import SandboxSimulation
from app.models.semantic_snapshot import SemanticSnapshot
from app.models.user import User
from app.models.v2.connection import Connection
from app.services.runtime import execution as execution_module
from app.services.runtime.action_bindings import _connection_identity as connection_identity
from app.services.runtime.action_bindings import publish_binding
from app.services.runtime.credentials import RuntimeContext, RuntimePrincipal
from app.services.runtime.execution import ExecutionError, execute_plan, record_plan_approval
from app.services.runtime.service import RuntimeService


def _fake_append_audit(connection, **kwargs) -> dict:
    return {"id": f"audit-{uuid.uuid4()}"}


def _fake_persist_idempotency(db, **kwargs) -> str:
    return "stored"


@contextmanager
def _fake_ledger_writes():
    """Fakes exactly `append_audit`/`persist_idempotency` — never
    `_resolve_connection_url`/`_fetch_expected_version`/`_writer_for_dialect`,
    which stay real for the duration of the wrapped `execute_plan` call. See
    this module's own docstring for why."""
    with mock.patch.object(execution_module, "append_audit", side_effect=_fake_append_audit), \
            mock.patch.object(execution_module, "persist_idempotency", side_effect=_fake_persist_idempotency):
        yield

_DIALECT_URL_ENV = {"postgresql": "RUNTIME_POSTGRES_URL", "mysql": "RUNTIME_MYSQL_URL"}
_DIALECT_SCHEMA = {"postgresql": "public", "mysql": "runtime"}
_DIALECT_KIND = {"postgresql": "postgres", "mysql": "mysql"}

_BASE_POLICY_DECISION = {
    "allowed": True, "reason_code": "ALLOW", "agent_capability": True, "user_entitlement": True,
    "freshness_state": "fresh", "freshness_lag_seconds": 30, "freshness_reason_code": "ALLOW",
    "requires_hitl": False,
}


def _require_dialect_url(dialect: str) -> str:
    url = os.environ.get(_DIALECT_URL_ENV[dialect])
    if not url:
        pytest.skip(f"{_DIALECT_URL_ENV[dialect]} is not configured")
    return url


def _raw_connect(dialect: str, url: str):
    if dialect == "postgresql":
        psycopg2 = pytest.importorskip("psycopg2")
        return psycopg2.connect(url)
    if dialect == "mysql":
        pymysql = pytest.importorskip("pymysql")
        from app.services.runtime.writers.mysql import _parse_mysql_url

        return pymysql.connect(**_parse_mysql_url(url), autocommit=False)
    raise AssertionError(f"unsupported dialect: {dialect!r}")


@contextmanager
def _seeded_target_row(dialect: str, url: str, *, target_id: str, tenant_id: str,
                        status: str = "pending", row_version: int = 1):
    """Insert one disposable `managed_targets` row for the duration of the
    `with` block, then delete it — never touching the shared `seed.sql` rows
    (same discipline as the Task 24/25 writer integration suites)."""
    connection = _raw_connect(dialect, url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO managed_targets (target_id, tenant_id, status, row_version, updated_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (target_id, tenant_id, status, row_version, datetime.now(timezone.utc)),
            )
        connection.commit()
    finally:
        connection.close()
    try:
        yield
    finally:
        cleanup = _raw_connect(dialect, url)
        try:
            with cleanup.cursor() as cursor:
                cursor.execute("DELETE FROM managed_targets WHERE target_id = %s", (target_id,))
            cleanup.commit()
        finally:
            cleanup.close()


def _read_target(dialect: str, url: str, target_id: str):
    connection = _raw_connect(dialect, url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, row_version FROM managed_targets WHERE target_id = %s", (target_id,),
            )
            return cursor.fetchone()
    finally:
        connection.close()


@dataclass(frozen=True)
class _GovernanceFixture:
    binding: ManagedActionBinding
    context: RuntimeContext
    snapshot_id: str
    release_id: str


def _seed_governance(db, dialect: str) -> _GovernanceFixture:
    """Seed the ORM governance rows a bound plan needs — mirrors
    `test_execution_service.py`'s own `_seed_baseline`, with per-call unique
    IDs so this can run for both dialects (and be repeated per test) in the
    same SQLite harness database without collisions."""
    suffix = uuid.uuid4().hex[:10]
    user_id = f"user-exec-{suffix}"
    agent_id = f"agent-exec-{suffix}"
    ontology_id = f"ontology-exec-{suffix}"
    release_id = f"release-exec-{suffix}"
    snapshot_id = f"snap-exec-{suffix}"
    action_id = f"action-exec-{suffix}"
    connection_id = f"connection-exec-{suffix}"
    domain = str(uuid.uuid4())

    user = User(
        id=user_id, username=user_id, email=f"{user_id}@example.invalid",
        password_hash="not-a-real-password-hash", role="editor", security_domain_id=domain,
    )
    client = OAuthClient(
        id=agent_id, client_name="Exec Agent", redirect_uris=[], allowed_scopes=[],
        is_active=True, created_by=user_id, security_domain_id=domain,
        allowed_audiences=[], capability_names=["investigate", "propose_action"],
    )
    db.add_all([user, client])
    db.commit()

    grant = OntologyDataGrant(
        id=str(uuid.uuid4()), ontology_id=ontology_id, user_id=user_id,
        capabilities=["investigate", "propose_action"], status="active", created_by=user_id,
    )
    db.add(grant)

    project = OntologyProject(
        id=ontology_id, name="exec ontology", domain="test", created_by=user.id,
        security_domain_id=user.security_domain_id,
    )
    db.add(project)
    db.flush()
    db.execute(
        text(
            "INSERT INTO ontology_releases "
            "(id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, status, created_by, created_at) "
            "VALUES (:id, :ontology_id, 1, 'v1', :manifest, '{}', :schema_hash, "
            "'published', :created_by, CURRENT_TIMESTAMP)"
        ),
        {
            "id": release_id, "ontology_id": ontology_id, "manifest": b"exec-manifest",
            "schema_hash": hashlib.sha256(release_id.encode()).digest(), "created_by": user.id,
        },
    )
    project.latest_published_release_id = release_id
    db.commit()

    snapshot = SemanticSnapshot(
        id=snapshot_id, ontology_release_id=release_id,
        quality_summary={"row_count": 1, "quality_score": 0.98}, evidence_summary={"citations": []},
        materialization_hash="a" * 64, status="materialized", created_by=user.id,
        freshness_state="fresh", freshness_lag_seconds=60,
        source_cursor={
            "source_id": "source-exec", "resource": "default", "contract": "watermark_primary_key",
            "watermark": None, "primary_key": "1", "opaque_value": None,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    db.add(snapshot)
    db.add(Action(id=action_id, ontology_id=ontology_id, name_cn="exec-target", enabled=True))
    db.add(Connection(id=connection_id, name="exec-db", kind=_DIALECT_KIND[dialect], status="active"))
    db.commit()

    binding = publish_binding(
        db, action_id=action_id, connection_id=connection_id,
        connection_target_identity=connection_identity(db.get(Connection, connection_id)),
        dialect=dialect, schema_name=_DIALECT_SCHEMA[dialect], table_name="managed_targets",
        primary_key_columns=["target_id"], writable_columns=["status"], version_column="row_version",
        parameter_schema={"status": "string"}, secret_ref="vault://kv/connections/exec-db#password",
    )

    principal = RuntimePrincipal(
        agent_id=agent_id, user_id=user_id, security_domain_id=domain,
        audience="ontexus-runtime", scope=frozenset({"ontology:read", "ontology:write"}),
        token_id=f"tok-{suffix}",
    )
    context = RuntimeContext(principal=principal, correlation_id=f"corr-{suffix}")
    return _GovernanceFixture(binding=binding, context=context, snapshot_id=snapshot_id, release_id=release_id)


def _seed_plan_and_sandbox(
    db, fixture: _GovernanceFixture, *, target_id: str, before_image: dict | None,
    plan_overrides: dict | None = None, sandbox_overrides: dict | None = None,
) -> str:
    """Hand-construct one `RuntimePlan` + `SandboxSimulation` pointing at
    `target_id` in the real fixture database — mirrors
    `test_execution_service.py`'s own `_seed_plan_and_sandbox` convention.
    Returns the plan id (resolved through `RuntimeService.get_action_plan`
    by each test, exactly like `execute_plan` itself does)."""
    binding = fixture.binding
    now = datetime.now(timezone.utc)
    target_key = [["target_id", target_id]]
    plan_kwargs = dict(
        id=f"plan-exec-{uuid.uuid4()}", semantic_snapshot_id=fixture.snapshot_id,
        ontology_release_id=fixture.release_id,
        agent_id=fixture.context.principal.agent_id, user_id=fixture.context.principal.user_id,
        action_id=binding.action_id,
        input_facts={}, evidence_citations=[],
        rule_outcomes=[{"rule_id": "action_eligibility", "result": "pass", "reason_code": "ALLOW"}],
        managed_action_binding_id=binding.managed_action_binding_id, binding_version=str(binding.version),
        parameters={"status": "approved"}, target_key=target_key,
        before_image_hash="b" * 64, version_hash="c" * 64,
        predicted_diff={"target_key": target_key, "before": before_image, "after": {"status": "approved"}},
        impact_scope={"instance_count": 1},
        risk_classification="single_instance_write",
        policy_decision=dict(_BASE_POLICY_DECISION),
        precondition_hashes=["m" * 64, "s" * 64, "b" * 64],
        expiry=now + timedelta(seconds=900),
        idempotency_key=f"idem-{uuid.uuid4()}",
        plan_hash="d" * 64,
        created_at=now,
    )
    plan_kwargs.update(plan_overrides or {})
    plan_row = RuntimePlan(**plan_kwargs)
    db.add(plan_row)
    db.commit()

    sandbox_kwargs = dict(
        id=str(uuid.uuid4()), action_plan_id=plan_row.id, semantic_snapshot_id=fixture.snapshot_id,
        ontology_release_id=fixture.release_id,
        agent_id=fixture.context.principal.agent_id, user_id=fixture.context.principal.user_id,
        managed_action_binding_id=binding.managed_action_binding_id, binding_version=str(binding.version),
        expected_rows=1, before_after_diff={"status": {"before": None, "after": "approved"}},
        impact_summary={"instance_count": 1}, rule_outcome=[], policy_result=dict(plan_kwargs["policy_decision"]),
        expires_at=plan_kwargs["expiry"],
        precondition_hashes={
            "plan_hash": plan_kwargs["plan_hash"], "before_image_hash": plan_kwargs["before_image_hash"],
            "version_hash": plan_kwargs["version_hash"], "materialization_hash": "a" * 64,
        },
        status="simulated",
    )
    sandbox_kwargs.update(sandbox_overrides or {})
    db.add(SandboxSimulation(**sandbox_kwargs))
    db.commit()
    return plan_row.id


# --- Automatic execution: a real end-to-end write ---------------------------


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_automatic_execution_writes_a_real_row(dialect, db):
    """Never mocks `_resolve_connection_url`/`_fetch_expected_version`/
    `_writer_for_dialect`: `execute_plan` really opens a connection to the
    disposable fixture database, reads the live `row_version`, and the real
    `PostgresRowWriter`/`MySQLRowWriter` really updates the row."""
    url = _require_dialect_url(dialect)
    target_id = f"target-exec-auto-{uuid.uuid4().hex[:8]}"

    with _seeded_target_row(dialect, url, target_id=target_id, tenant_id="tenant-exec-auto"):
        fixture = _seed_governance(db, dialect)
        plan_id = _seed_plan_and_sandbox(
            db, fixture, target_id=target_id, before_image={"status": "pending"},
        )
        plan = RuntimeService().get_action_plan(plan_id, fixture.context, db)

        with _fake_ledger_writes():
            receipt = execute_plan(
                db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=fixture.context,
            )

        assert receipt.status == "SUCCEEDED"
        assert receipt.execution_class == "AUTOMATIC"
        assert receipt.dialect == dialect
        assert receipt.audit_id
        assert receipt.reconciliation_case_id is None

        row = _read_target(dialect, url, target_id)
        assert row is not None
        assert row[0] == "approved"


# --- HITL: approve, then execute, against a real database -------------------


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_hitl_approve_then_execute_writes_a_real_row(dialect, db):
    url = _require_dialect_url(dialect)
    target_id = f"target-exec-hitl-{uuid.uuid4().hex[:8]}"

    with _seeded_target_row(dialect, url, target_id=target_id, tenant_id="tenant-exec-hitl"):
        fixture = _seed_governance(db, dialect)
        plan_id = _seed_plan_and_sandbox(
            db, fixture, target_id=target_id, before_image={"status": "pending"},
            plan_overrides={
                "policy_decision": {**_BASE_POLICY_DECISION, "freshness_state": "stale", "requires_hitl": True},
            },
        )
        plan = RuntimeService().get_action_plan(plan_id, fixture.context, db)

        # No recorded approval yet -> denied, and no real connection opened
        # (POLICY_DENIED is raised before the idempotency/fence step).
        with pytest.raises(ExecutionError) as exc:
            execute_plan(db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=fixture.context)
        assert exc.value.reason_code == "POLICY_DENIED"

        record_plan_approval(
            db, plan_id=plan.id, presented_plan_hash=plan.plan_hash,
            approver_context=fixture.context, now=datetime.now(timezone.utc),
        )

        with _fake_ledger_writes():
            receipt = execute_plan(
                db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=fixture.context,
            )
        assert receipt.status == "SUCCEEDED"
        assert receipt.execution_class == "HUMAN_APPROVED"
        assert receipt.dialect == dialect

        row = _read_target(dialect, url, target_id)
        assert row is not None
        assert row[0] == "approved"


# --- Precondition conflict against a real database (no live target row) -----


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_precondition_conflict_when_live_target_row_is_missing(dialect, db):
    """Unlike `test_execution_service.py`'s SQLite-mocked `row-count-mismatch`
    case (a `Sandbox.expected_rows != 1` preflight rejection that never opens
    a connection), this never seeds a `managed_targets` row at all: the real
    `_fetch_expected_version` SELECT against the real database finds zero
    matching rows post-fence, and `execute_plan` marks the execution `FAILED`
    with a real `PRECONDITION_CONFLICT`."""
    url = _require_dialect_url(dialect)
    target_id = f"target-exec-missing-{uuid.uuid4().hex[:8]}"  # never inserted

    fixture = _seed_governance(db, dialect)
    plan_id = _seed_plan_and_sandbox(db, fixture, target_id=target_id, before_image={"status": "pending"})
    plan = RuntimeService().get_action_plan(plan_id, fixture.context, db)

    with _fake_ledger_writes():
        receipt = execute_plan(db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=fixture.context)

    assert receipt.status == "FAILED"
    assert receipt.writer_receipt == {"reason_code": "PRECONDITION_CONFLICT"}
    assert receipt.reconciliation_case_id is None
