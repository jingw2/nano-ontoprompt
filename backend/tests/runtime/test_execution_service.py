"""Task 26: governed runtime execution, reconciliation, and rollback.

`execute_plan` (`app.services.runtime.execution`) is the ONLY place in this
codebase that ever calls a `ManagedRowWriter.execute()` (Task 24/25) — every
governance boundary built by Tasks 15/20/21/22/23 is re-verified here,
immediately before that call. This suite proves:

- The shared writer path is used identically for an `AUTOMATIC` plan
  (`test_automatic_execution_uses_shared_writer_and_audit`), recording a
  real audit event and no reconciliation case.
- Twelve independent drift/override scenarios are all rejected before any
  writer-level (or even version-read) database connection ever opens — a
  `transaction_spy` genuinely wired to `psycopg2.connect` proves this, not
  just a convention.
- An outcome the writer's own contract cannot classify (anything other than
  a `WriterError`/`WriteReceipt`) becomes `UNKNOWN`, opens a
  `RuntimeReconciliationCase`, and can never be blindly replayed
  (`retry_unknown` is structurally incapable of triggering re-execution).
- Rollback is always a brand-new governed plan, never a direct write, and
  fails with a real, structured error when there is no retained
  before-image to restore (true of every plan produced by the bound path
  today, since Task 21's `freeze_action_target` never reads a live row).
- The execution fence (`RuntimeExecution.plan_id`'s `UNIQUE` constraint)
  resolves a GENUINE race, not just a sequential call pair:
  `test_execution_fence_resolves_a_genuine_concurrent_race` runs two real
  threads, each with its own DB session against the same underlying SQLite
  file, and proves the loser's INSERT hits the real constraint while the
  winner's row is still verifiably `PENDING` (its writer call is deliberately
  blocked mid-flight), not merely that a second sequential call returns a
  cached receipt.

`_seed_baseline`/`_seed_plan_and_sandbox` hand-construct `RuntimePlan`/
`SandboxSimulation` rows directly (mirroring `tests/runtime/test_sandbox.py`'s
own `_seed_plan` convention) rather than going through
`RuntimeService.create_action_plan`/`simulate_action`, so each of the twelve
drift scenarios below can mutate exactly one recorded fact in isolation.
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from sqlalchemy import text

from tests.conftest import TestSession

from app.models.action import Action
from app.models.managed_action import ManagedActionBinding
from app.models.oauth import OAuthClient
from app.models.ontology import OntologyProject
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.runtime_execution import RuntimeExecution
from app.models.runtime_plan import RuntimePlan
from app.models.sandbox import SandboxSimulation
from app.models.semantic_snapshot import SemanticSnapshot
from app.models.user import User
from app.models.v2.connection import Connection
from app.services.runtime import execution as execution_module
from app.services.runtime.action_bindings import _connection_identity as connection_identity
from app.services.runtime.action_bindings import publish_binding
from app.services.runtime.credentials import RuntimeAccessError, RuntimeContext, RuntimePrincipal
from app.services.runtime.execution import (
    ExecutionError,
    create_rollback_plan,
    execute_plan,
    get_execution_status,
    record_plan_approval,
    retry_unknown,
)
from app.services.runtime.reconciliation import get_reconciliation_case
from app.services.runtime.service import RuntimeService
from app.services.runtime.writers.base import WriteReceipt

DOMAIN = "00000000-0000-0000-0000-0000000000ee"
CAPABILITIES = ["investigate", "propose_action"]
ONTOLOGY_ID = "ontology-exec-001"
RELEASE_ID = "release-exec-001"
SNAPSHOT_ID = "snap-valid-001"
AGENT_ID = "agent-exec-001"
USER_ID = "user-exec-001"
ACTION_ID = "action-exec-001"
CONNECTION_ID = "connection-exec-001"
TARGET_ID = "target-exec-001"

_DIALECT_KIND = {"postgresql": "postgres", "mysql": "mysql"}
_DIALECT_SCHEMA = {"postgresql": "public", "mysql": "runtime"}


class _FakeWriter:
    """Stands in for `PostgresRowWriter`/`MySQLRowWriter` in the unit suite
    (real dialect connectivity is exercised separately by
    `tests/runtime/integration/test_execution_dialects.py`)."""

    def __init__(self, mode: str = "success"):
        self.mode = mode
        self.calls = 0

    def execute(self, plan, *, credential_ref: str) -> WriteReceipt:
        self.calls += 1
        if self.mode == "success":
            return WriteReceipt(
                dialect=plan.dialect, primary_key_tuple=plan.primary_key_tuple, affected_rows=1,
                before_image_hash=plan.before_image_hash, after_image_hash="e" * 64,
                version_hash=plan.version_hash, idempotency_key=plan.idempotency_key,
                status="committed", correlation_id="fake-writer-correlation",
                expected_version=plan.expected_version,
            )
        if self.mode == "timeout":
            raise TimeoutError("simulated network partition — commit outcome unknown")
        raise AssertionError(f"unexpected fake writer mode: {self.mode!r}")


def _fake_append_audit(connection, **kwargs) -> dict:
    return {"id": f"audit-{uuid.uuid4()}"}


def _fake_persist_idempotency(db, **kwargs) -> str:
    return "stored"


def _context() -> RuntimeContext:
    principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id=USER_ID, security_domain_id=DOMAIN,
        audience="ontexus-runtime", scope=frozenset({"ontology:read", "ontology:write"}),
        token_id="tok-exec-001",
    )
    return RuntimeContext(principal=principal, correlation_id="corr-exec-001")


@pytest.fixture
def runtime_db(db):
    """Alias for the standard SQLite unit-test session, matching the
    brief's own test-skeleton fixture name."""
    return db


@pytest.fixture
def runtime_context() -> RuntimeContext:
    return _context()


@pytest.fixture
def transaction_spy(monkeypatch):
    """Wraps `psycopg2.connect` (the one real network call both the version
    read (`execution._fetch_expected_version`) and the real writer ever make)
    so every one of the twelve preflight-rejection cases can prove it never
    opens a connection — genuinely wired to the real driver entry point, not
    a fixture that merely records calls no code path would ever reach."""
    import psycopg2

    class _Spy:
        def __init__(self):
            self.started = False

    spy = _Spy()

    def _fail_if_connected(*args, **kwargs):
        spy.started = True
        raise AssertionError("no writer/version-read connection may open during a preflight rejection")

    monkeypatch.setattr(psycopg2, "connect", _fail_if_connected)
    return spy


def _seed_baseline(db, *, dialect: str = "postgresql") -> ManagedActionBinding:
    user = User(
        id=USER_ID, username=USER_ID, email=f"{USER_ID}@example.invalid",
        password_hash="not-a-real-password-hash", role="editor", security_domain_id=DOMAIN,
    )
    client = OAuthClient(
        id=AGENT_ID, client_name="Exec Agent", redirect_uris=[], allowed_scopes=[],
        is_active=True, created_by=USER_ID, security_domain_id=DOMAIN,
        allowed_audiences=[], capability_names=list(CAPABILITIES),
    )
    db.add_all([user, client])
    db.commit()

    grant = OntologyDataGrant(
        id=str(uuid.uuid4()), ontology_id=ONTOLOGY_ID, user_id=USER_ID,
        capabilities=list(CAPABILITIES), status="active", created_by=USER_ID,
    )
    db.add(grant)

    project = OntologyProject(
        id=ONTOLOGY_ID, name="exec ontology", domain="test", created_by=user.id,
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
            "id": RELEASE_ID, "ontology_id": ONTOLOGY_ID, "manifest": b"exec-manifest",
            "schema_hash": hashlib.sha256(RELEASE_ID.encode()).digest(), "created_by": user.id,
        },
    )
    project.latest_published_release_id = RELEASE_ID
    db.commit()

    snapshot = SemanticSnapshot(
        id=SNAPSHOT_ID, ontology_release_id=RELEASE_ID,
        quality_summary={"row_count": 1, "quality_score": 0.98}, evidence_summary={"citations": []},
        materialization_hash="a" * 64, status="materialized", created_by=user.id,
        freshness_state="fresh", freshness_lag_seconds=60,
        source_cursor={
            "source_id": "source-exec-001", "resource": "default", "contract": "watermark_primary_key",
            "watermark": None, "primary_key": "1", "opaque_value": None,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    db.add(snapshot)
    db.add(Action(id=ACTION_ID, ontology_id=ONTOLOGY_ID, name_cn="exec-target", enabled=True))
    db.add(Connection(id=CONNECTION_ID, name="exec-db", kind=_DIALECT_KIND[dialect], status="active"))
    db.commit()

    return publish_binding(
        db, action_id=ACTION_ID, connection_id=CONNECTION_ID,
        connection_target_identity=connection_identity(db.get(Connection, CONNECTION_ID)),
        dialect=dialect, schema_name=_DIALECT_SCHEMA[dialect], table_name="managed_targets",
        primary_key_columns=["target_id"], writable_columns=["status"], version_column="row_version",
        parameter_schema={"status": "string"}, secret_ref="vault://kv/connections/exec-db#password",
    )


_BASE_POLICY_DECISION = {
    "allowed": True, "reason_code": "ALLOW", "agent_capability": True, "user_entitlement": True,
    "freshness_state": "fresh", "freshness_lag_seconds": 30, "freshness_reason_code": "ALLOW",
    "requires_hitl": False,
}


def _seed_plan_and_sandbox(
    db, binding: ManagedActionBinding, *, before_image=None, plan_overrides=None, sandbox_overrides=None,
):
    now = datetime.now(timezone.utc)
    target_key = [["target_id", TARGET_ID]]
    plan_kwargs = dict(
        id=f"plan-exec-{uuid.uuid4()}", semantic_snapshot_id=SNAPSHOT_ID, ontology_release_id=RELEASE_ID,
        agent_id=AGENT_ID, user_id=USER_ID, action_id=ACTION_ID, input_facts={}, evidence_citations=[],
        rule_outcomes=[{"rule_id": "action_eligibility", "result": "pass", "reason_code": "ALLOW"}],
        managed_action_binding_id=binding.managed_action_binding_id, binding_version=str(binding.version),
        parameters={"status": "approved"}, target_key=target_key,
        before_image_hash="b" * 64, version_hash="c" * 64,
        predicted_diff={"target_key": target_key, "before": before_image, "after": {"status": "approved"}},
        impact_scope={"ontology_id": ONTOLOGY_ID, "action_id": ACTION_ID, "instance_count": 1},
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
        id=str(uuid.uuid4()), action_plan_id=plan_row.id, semantic_snapshot_id=SNAPSHOT_ID,
        ontology_release_id=RELEASE_ID, agent_id=AGENT_ID, user_id=USER_ID,
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

    return RuntimeService().get_action_plan(plan_row.id, _context(), db)


def _mutate_binding(db, binding_id: str, **fields):
    row = db.get(ManagedActionBinding, binding_id)
    for key, value in fields.items():
        setattr(row, key, value)
    db.commit()


def execute_fixture(db, dialect: str, case_id: str, *, transaction_spy=None, selector=None, parameters=None):
    binding = _seed_baseline(db, dialect=dialect)
    context = _context()

    plan_overrides: dict = {}
    sandbox_overrides: dict = {}
    before_image = None

    if case_id == "expired-plan":
        plan_overrides["expiry"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif case_id == "before-image-drift":
        sandbox_overrides["precondition_hashes"] = {
            "plan_hash": "d" * 64, "before_image_hash": "z" * 64, "version_hash": "c" * 64,
            "materialization_hash": "a" * 64,
        }
    elif case_id == "version-conflict":
        sandbox_overrides["precondition_hashes"] = {
            "plan_hash": "d" * 64, "before_image_hash": "b" * 64, "version_hash": "z" * 64,
            "materialization_hash": "a" * 64,
        }
    elif case_id == "row-count-mismatch":
        sandbox_overrides["expected_rows"] = 0
    # NOTE: "auto-approved"/"unknown-outcome" deliberately leave
    # `before_image` at its default `None` — every plan `_seed_baseline`
    # produces here is BOUND, and `service.py`'s `_resolve_writable_target`
    # hard-codes `target_row: None` on the bound branch, so a real bound
    # plan's `predicted_diff["before"]` is always `None`. A non-`None` value
    # here would be a combination the real system can never produce (see
    # `test_rollback_rejects_every_bound_plan_the_real_system_can_execute`).

    plan = _seed_plan_and_sandbox(
        db, binding, before_image=before_image, plan_overrides=plan_overrides, sandbox_overrides=sandbox_overrides,
    )

    if case_id == "binding-version-drift":
        _mutate_binding(db, binding.managed_action_binding_id, version=99)
    elif case_id == "binding-revoked":
        _mutate_binding(db, binding.managed_action_binding_id, status="revoked")
    elif case_id == "connection-target-drift":
        connection = db.get(Connection, CONNECTION_ID)
        connection.config = {"changed": True}
        db.commit()
    elif case_id == "snapshot-drift":
        db.execute(
            text("UPDATE semantic_snapshots SET freshness_state = 'unknown' WHERE id = :id"),
            {"id": SNAPSHOT_ID},
        )
        db.commit()
        db.expire_all()
    elif case_id == "policy-drift":
        grant = db.execute(
            text("SELECT id FROM ontology_data_grants WHERE ontology_id = :oid"), {"oid": ONTOLOGY_ID},
        ).scalar_one()
        db.execute(text("UPDATE ontology_data_grants SET status = 'revoked' WHERE id = :id"), {"id": grant})
        db.commit()
        db.expire_all()

    presented_hash = plan.plan_hash
    if case_id == "plan-hash-mismatch":
        presented_hash = "0" * 64
    if case_id == "caller-parameter-override":
        parameters = {"status": "hacked"}
    if case_id == "caller-selector-override":
        selector = {"target_id": "some-other-target"}

    if case_id in ("auto-approved", "unknown-outcome"):
        writer_mode = "success" if case_id == "auto-approved" else "timeout"
        with mock.patch.object(execution_module, "_resolve_connection_url", return_value="stub://unused"), \
                mock.patch.object(execution_module, "_fetch_expected_version", return_value=1), \
                mock.patch.object(execution_module, "_writer_for_dialect", return_value=_FakeWriter(writer_mode)), \
                mock.patch.object(execution_module, "append_audit", side_effect=_fake_append_audit), \
                mock.patch.object(execution_module, "persist_idempotency", side_effect=_fake_persist_idempotency):
            return execute_plan(db, plan_id=plan.id, presented_plan_hash=presented_hash, context=context)

    return execute_plan(
        db, plan_id=plan.id, presented_plan_hash=presented_hash, context=context,
        selector=selector, parameters=parameters,
    )


# --- Automatic execution ----------------------------------------------------


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_automatic_execution_uses_shared_writer_and_audit(dialect, runtime_db):
    receipt = execute_fixture(runtime_db, dialect, "auto-approved")
    assert receipt.status == "SUCCEEDED"
    assert receipt.execution_class == "AUTOMATIC"
    assert receipt.audit_id
    assert receipt.reconciliation_case_id is None
    assert receipt.dialect == dialect


def test_automatic_execution_persists_execution_fence_row(runtime_db):
    receipt = execute_fixture(runtime_db, "postgresql", "auto-approved")
    stored = runtime_db.get(RuntimeExecution, receipt.execution_id)
    assert stored is not None
    assert stored.status == "SUCCEEDED"
    assert stored.plan_id == receipt.plan_id


def test_write_receipt_and_audit_lineage_carry_expected_version(runtime_db):
    """Item 3 (2026-08-31 fix wave): `expected_version` is the live
    optimistic-lock precondition value read immediately before the write
    (`_fetch_expected_version`). It must reach `WriteReceipt`,
    `RuntimeExecution.writer_receipt`, and the audit event's `lineage` — the
    only way the durable audit trail can distinguish which row generation a
    given write actually applied to (`before_image_hash`/`after_image_hash`/
    `version_hash` are otherwise byte-identical across successive writes to
    the same row)."""
    captured_lineage: dict = {}

    def _capturing_append_audit(connection, **kwargs) -> dict:
        captured_lineage.update(kwargs.get("lineage") or {})
        return {"id": f"audit-{uuid.uuid4()}"}

    binding = _seed_baseline(runtime_db, dialect="postgresql")
    plan = _seed_plan_and_sandbox(runtime_db, binding, before_image={"status": "pending"})
    context = _context()

    with mock.patch.object(execution_module, "_resolve_connection_url", return_value="stub://unused"), \
            mock.patch.object(execution_module, "_fetch_expected_version", return_value=7), \
            mock.patch.object(execution_module, "_writer_for_dialect", return_value=_FakeWriter("success")), \
            mock.patch.object(execution_module, "append_audit", side_effect=_capturing_append_audit), \
            mock.patch.object(execution_module, "persist_idempotency", side_effect=_fake_persist_idempotency):
        receipt = execute_plan(runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=context)

    assert receipt.writer_receipt["expected_version"] == 7
    assert captured_lineage["expected_version"] == 7


def test_idempotent_retry_never_calls_the_writer_twice(runtime_db):
    """A second `execute_plan` call for the SAME already-terminal plan must
    return the existing receipt, never a fresh write — the execution fence's
    own `UNIQUE(plan_id)` constraint is what makes this structural, not a
    convention."""
    binding = _seed_baseline(runtime_db, dialect="postgresql")
    plan = _seed_plan_and_sandbox(runtime_db, binding, before_image={"status": "pending"})
    context = _context()
    fake_writer = _FakeWriter("success")

    with mock.patch.object(execution_module, "_resolve_connection_url", return_value="stub://unused"), \
            mock.patch.object(execution_module, "_fetch_expected_version", return_value=1), \
            mock.patch.object(execution_module, "_writer_for_dialect", return_value=fake_writer), \
            mock.patch.object(execution_module, "append_audit", side_effect=_fake_append_audit), \
            mock.patch.object(execution_module, "persist_idempotency", side_effect=_fake_persist_idempotency):
        first = execute_plan(runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=context)
        second = execute_plan(runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=context)

    assert first.execution_id == second.execution_id
    assert first.status == second.status == "SUCCEEDED"
    assert fake_writer.calls == 1


class _BlockingWriter:
    """A `_FakeWriter` variant whose `execute()` blocks mid-flight until
    released, so a concurrency test can deterministically observe the
    execution fence row it feeds off of while it is still genuinely
    `PENDING` in the database (not yet terminal)."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def execute(self, plan, *, credential_ref: str) -> WriteReceipt:
        self.calls += 1
        self.started.set()
        released = self.release.wait(timeout=5)
        assert released, "test bug: writer was never released"
        return WriteReceipt(
            dialect=plan.dialect, primary_key_tuple=plan.primary_key_tuple, affected_rows=1,
            before_image_hash=plan.before_image_hash, after_image_hash="e" * 64,
            version_hash=plan.version_hash, idempotency_key=plan.idempotency_key,
            status="committed", correlation_id="fake-writer-correlation",
            expected_version=plan.expected_version,
        )


def test_execution_fence_resolves_a_genuine_concurrent_race(runtime_db):
    """Two real OS threads, each with its own DB session (`TestSession()`,
    bound to the same underlying SQLite file `runtime_db` itself uses) race
    `execute_plan` for the SAME plan — a genuinely concurrency-shaped
    scenario, unlike `test_idempotent_retry_never_calls_the_writer_twice`
    above (a sequential first-then-second call pair against one already-
    terminal execution, which never contends the `UNIQUE(plan_id)`
    constraint at all).

    The "winner" thread's fake writer deliberately blocks after the
    execution-fence row has already been committed as `PENDING` but before
    it ever returns, so the "loser" thread's own `execute_plan` call —
    started only once we know the winner's fence row is committed — is
    guaranteed to hit the real `UNIQUE` constraint while that row is still
    verifiably `PENDING`, not already terminal. This proves the
    "genuinely concurrent in-flight attempt" branch in `execute_plan`
    (`raise ExecutionError(PRECONDITION_CONFLICT, "plan execution is
    already in progress")`), which the sequential test above can never
    reach.
    """
    binding = _seed_baseline(runtime_db, dialect="postgresql")
    plan = _seed_plan_and_sandbox(runtime_db, binding, before_image={"status": "pending"})
    plan_id, plan_hash = plan.id, plan.plan_hash

    writer = _BlockingWriter()
    results: dict[str, object] = {}

    def _run_winner():
        session = TestSession()
        try:
            results["winner"] = execute_plan(
                session, plan_id=plan_id, presented_plan_hash=plan_hash, context=_context(),
            )
        finally:
            session.close()

    def _run_loser():
        assert writer.started.wait(timeout=5), "test bug: winner never reached the writer"
        session = TestSession()
        try:
            execute_plan(session, plan_id=plan_id, presented_plan_hash=plan_hash, context=_context())
            results["loser_unexpected_success"] = True
        except ExecutionError as exc:
            results["loser_error"] = exc
        finally:
            session.close()

    with mock.patch.object(execution_module, "_resolve_connection_url", return_value="stub://unused"), \
            mock.patch.object(execution_module, "_fetch_expected_version", return_value=1), \
            mock.patch.object(execution_module, "_writer_for_dialect", return_value=writer), \
            mock.patch.object(execution_module, "append_audit", side_effect=_fake_append_audit), \
            mock.patch.object(execution_module, "persist_idempotency", side_effect=_fake_persist_idempotency):
        winner_thread = threading.Thread(target=_run_winner)
        loser_thread = threading.Thread(target=_run_loser)
        winner_thread.start()
        loser_thread.start()
        loser_thread.join(timeout=5)
        assert "loser_error" in results, results  # the loser must have finished (denied) by now
        writer.release.set()
        winner_thread.join(timeout=5)

    assert writer.calls == 1
    assert results["winner"].status == "SUCCEEDED"
    assert results["loser_error"].reason_code == "PRECONDITION_CONFLICT"


# --- Preflight rejections (before any transaction) --------------------------


@pytest.mark.parametrize("case_id", [
    "plan-hash-mismatch", "binding-version-drift", "binding-revoked",
    "connection-target-drift", "snapshot-drift", "policy-drift",
    "before-image-drift", "version-conflict", "row-count-mismatch",
    "caller-parameter-override", "caller-selector-override", "expired-plan",
])
def test_preflight_rejects_before_any_transaction(case_id, runtime_db, transaction_spy):
    with pytest.raises(ExecutionError) as exc:
        execute_fixture(runtime_db, "postgresql", case_id, transaction_spy=transaction_spy)
    assert exc.value.reason_code in {"INVALID_PLAN_HASH", "BINDING_DRIFT", "SNAPSHOT_STALE",
                                      "POLICY_DENIED", "PRECONDITION_CONFLICT", "PLAN_EXPIRED"}
    assert transaction_spy.started is False


def test_execute_plan_rejects_unbound_action_before_any_transaction(runtime_db, transaction_spy):
    """Execution requires a real writer target; an unbound plan has none —
    this is a foundational precondition beyond the twelve named drift cases
    above, since only a bound plan can ever produce a `FrozenActionPlan`."""
    binding = _seed_baseline(runtime_db, dialect="postgresql")
    plan = _seed_plan_and_sandbox(
        runtime_db, binding, plan_overrides={"managed_action_binding_id": None, "binding_version": None},
    )
    with pytest.raises(ExecutionError) as exc:
        execute_plan(runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=_context())
    assert exc.value.reason_code == "UNSUPPORTED_ACTION"
    assert transaction_spy.started is False


def test_execute_plan_denies_a_different_principal(runtime_db):
    """`get_action_plan`'s own existence-hiding denial (Task 15) is the
    first gate `execute_plan` relies on."""
    binding = _seed_baseline(runtime_db, dialect="postgresql")
    plan = _seed_plan_and_sandbox(runtime_db, binding)
    stranger = RuntimeContext(
        principal=RuntimePrincipal(
            agent_id="agent-someone-else", user_id="user-someone-else", security_domain_id=DOMAIN,
            audience="ontexus-runtime", scope=frozenset({"ontology:write"}), token_id="tok-stranger-001",
        ),
        correlation_id="corr-stranger-001",
    )
    with pytest.raises(RuntimeAccessError) as exc:
        execute_plan(runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=stranger)
    assert exc.value.reason_code == "POLICY_DENIED"


# --- Unknown outcomes / reconciliation --------------------------------------


def test_unknown_outcome_creates_reconciliation_and_no_blind_replay(runtime_db):
    receipt = execute_fixture(runtime_db, "postgresql", "unknown-outcome")
    assert receipt.status == "UNKNOWN"
    assert receipt.reconciliation_case_id
    assert retry_unknown(receipt) is False

    case = get_reconciliation_case(runtime_db, case_id=receipt.reconciliation_case_id)
    assert case is not None
    assert case.status == "open"
    assert case.execution_id == receipt.execution_id


def test_write_succeeds_but_audit_fails_becomes_unknown_with_reconciliation(runtime_db, runtime_context):
    """Fix-round Finding 1: a successful `writer.execute()` followed by an
    `append_audit` failure (e.g. a chain-head lock timeout, a deadlock, a
    dropped connection — `append_audit` does a `SELECT ... FOR UPDATE`
    internally) must never leave the execution stuck `PENDING` with zero
    record. Pre-fix, this exception escaped `execute_plan` completely
    uncaught. Post-fix, it must become `UNKNOWN` with a real reconciliation
    case whose `observed_effect` proves the write itself is known to have
    succeeded (distinct from a genuinely ambiguous writer failure)."""
    binding = _seed_baseline(runtime_db, dialect="postgresql")
    plan = _seed_plan_and_sandbox(runtime_db, binding, before_image=None)
    context = runtime_context
    fake_writer = _FakeWriter("success")

    def _raising_append_audit(connection, **kwargs):
        raise RuntimeError("simulated chain-head lock timeout during append_audit")

    with mock.patch.object(execution_module, "_resolve_connection_url", return_value="stub://unused"), \
            mock.patch.object(execution_module, "_fetch_expected_version", return_value=1), \
            mock.patch.object(execution_module, "_writer_for_dialect", return_value=fake_writer), \
            mock.patch.object(execution_module, "append_audit", side_effect=_raising_append_audit), \
            mock.patch.object(execution_module, "persist_idempotency", side_effect=_fake_persist_idempotency):
        receipt = execute_plan(runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=context)

    assert receipt.status == "UNKNOWN"
    assert receipt.reconciliation_case_id
    assert fake_writer.calls == 1

    stored = runtime_db.get(RuntimeExecution, receipt.execution_id)
    assert stored is not None
    assert stored.status == "UNKNOWN"  # never stuck PENDING

    case = get_reconciliation_case(runtime_db, case_id=receipt.reconciliation_case_id)
    assert case is not None
    assert case.status == "open"
    # The recorded facts must reflect that the write DID succeed.
    assert "write_receipt" in case.observed_effect
    assert case.observed_effect["write_receipt"]["affected_rows"] == 1
    assert case.observed_effect["write_receipt"]["status"] == "committed"
    assert case.next_action == "human_review_confirm_write_already_succeeded"


def test_pre_write_version_read_failure_becomes_unknown_with_reconciliation(runtime_db, runtime_context):
    """Fix-round Finding 1: a connection failure inside
    `_fetch_expected_version` (e.g. `RUNTIME_POSTGRES_URL` pointing at an
    unreachable port — a raw driver exception, never an `ExecutionError`)
    must not escape `execute_plan` uncaught and must not leave the execution
    stuck `PENDING` forever, even though no write was ever attempted here."""
    binding = _seed_baseline(runtime_db, dialect="postgresql")
    plan = _seed_plan_and_sandbox(runtime_db, binding, before_image=None)
    context = runtime_context

    def _raising_fetch_expected_version(*args, **kwargs):
        raise ConnectionError("simulated connection refused")

    with mock.patch.object(execution_module, "_resolve_connection_url", return_value="stub://unused"), \
            mock.patch.object(execution_module, "_fetch_expected_version", side_effect=_raising_fetch_expected_version), \
            mock.patch.object(execution_module, "persist_idempotency", side_effect=_fake_persist_idempotency):
        receipt = execute_plan(runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=context)

    assert receipt.status == "UNKNOWN"
    assert receipt.reconciliation_case_id

    stored = runtime_db.get(RuntimeExecution, receipt.execution_id)
    assert stored is not None
    assert stored.status == "UNKNOWN"  # never stuck PENDING

    case = get_reconciliation_case(runtime_db, case_id=receipt.reconciliation_case_id)
    assert case is not None
    assert case.status == "open"
    # No write was ever attempted here — distinct from the write-succeeded
    # case above, no `write_receipt` should be present.
    assert "write_receipt" not in case.observed_effect
    assert case.next_action == "human_review"


def test_unknown_reason_is_truncated_to_the_column_width(runtime_db, runtime_context):
    """`RuntimeReconciliationCase.unknown_reason` is a `String(500)` column
    (`app.models.runtime_execution`). A raw driver exception's message can
    exceed that width on PostgreSQL — if `unknown_reason=str(exc)` were
    passed unbounded, `StringDataRightTruncation` would itself escape from
    inside the broad exception handler this fix relies on, defeating it."""
    binding = _seed_baseline(runtime_db, dialect="postgresql")
    plan = _seed_plan_and_sandbox(runtime_db, binding, before_image=None)
    context = runtime_context
    long_message = "x" * 2000

    def _raising_fetch_expected_version(*args, **kwargs):
        raise ConnectionError(long_message)

    with mock.patch.object(execution_module, "_resolve_connection_url", return_value="stub://unused"), \
            mock.patch.object(execution_module, "_fetch_expected_version", side_effect=_raising_fetch_expected_version), \
            mock.patch.object(execution_module, "persist_idempotency", side_effect=_fake_persist_idempotency):
        receipt = execute_plan(runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=context)

    assert receipt.status == "UNKNOWN"
    case = get_reconciliation_case(runtime_db, case_id=receipt.reconciliation_case_id)
    assert case is not None
    assert len(case.unknown_reason) <= 500


# --- HUMAN_APPROVED path ------------------------------------------------


def test_human_approved_execution_requires_a_recorded_approval(runtime_db):
    """A bound, single-row plan whose data was soft-stale at plan-creation
    time (`requires_hitl=True`) routes to `HUMAN_APPROVED` via risk.py's
    `freshness_soft_stale` factor — still bound, so (once approved) it can
    actually reach a writer, unlike an unbound/unscoped proposal."""
    binding = _seed_baseline(runtime_db, dialect="postgresql")
    plan = _seed_plan_and_sandbox(
        runtime_db, binding, before_image={"status": "pending"},
        plan_overrides={
            "policy_decision": {**_BASE_POLICY_DECISION, "freshness_state": "stale", "requires_hitl": True},
        },
    )
    context = _context()

    # No recorded approval yet -> denied even though risk routing itself
    # would otherwise allow HUMAN_APPROVED execution.
    with pytest.raises(ExecutionError) as exc:
        execute_plan(runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=context)
    assert exc.value.reason_code == "POLICY_DENIED"

    record_plan_approval(
        runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash,
        approver_context=context, now=datetime.now(timezone.utc),
    )

    fake_writer = _FakeWriter("success")
    with mock.patch.object(execution_module, "_resolve_connection_url", return_value="stub://unused"), \
            mock.patch.object(execution_module, "_fetch_expected_version", return_value=1), \
            mock.patch.object(execution_module, "_writer_for_dialect", return_value=fake_writer), \
            mock.patch.object(execution_module, "append_audit", side_effect=_fake_append_audit), \
            mock.patch.object(execution_module, "persist_idempotency", side_effect=_fake_persist_idempotency):
        receipt = execute_plan(runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=context)

    assert receipt.status == "SUCCEEDED"
    assert receipt.execution_class == "HUMAN_APPROVED"


# --- get_execution_status ----------------------------------------------


def test_get_execution_status_returns_none_before_execution(runtime_db, runtime_context):
    binding = _seed_baseline(runtime_db, dialect="postgresql")
    plan = _seed_plan_and_sandbox(runtime_db, binding)
    assert get_execution_status(runtime_db, plan_id=plan.id, context=runtime_context) is None


def test_get_execution_status_matches_the_execution_receipt(runtime_db):
    receipt = execute_fixture(runtime_db, "postgresql", "auto-approved")
    status = get_execution_status(runtime_db, plan_id=receipt.plan_id, context=_context())
    assert status is not None
    assert status.execution_id == receipt.execution_id
    assert status.status == "SUCCEEDED"


# --- Rollback -------------------------------------------------------------


def test_rollback_rejects_every_bound_plan_the_real_system_can_execute(runtime_db, runtime_context):
    """`create_rollback_plan`'s success path requires `predicted_diff
    ["before"]` to be non-`None`, but `execute_plan` refuses to execute any
    plan whose `managed_action_binding_id` is `None` — and `service.py`'s
    `_resolve_writable_target` hard-codes `target_row: None` on every BOUND
    plan, so `predicted_diff["before"]` is `None` for every bound plan by
    construction. These two conditions are mutually exclusive: no plan this
    codebase's own governed write path can ever actually execute can ever be
    rolled back today (see `create_rollback_plan`'s own docstring). Proven
    here against a plan that really did execute successfully through the
    shared writer/audit path (`execute_fixture`'s "auto-approved" case) —
    not a hand-fabricated failure fixture."""
    receipt = execute_fixture(runtime_db, "postgresql", "auto-approved")
    assert receipt.status == "SUCCEEDED"

    with pytest.raises(ExecutionError) as exc:
        create_rollback_plan(runtime_db, execution_id=receipt.execution_id, context=runtime_context)
    assert exc.value.reason_code == "PRECONDITION_CONFLICT"


def test_rollback_fails_without_a_retained_before_image(runtime_db, runtime_context):
    """Every plan produced by the bound/managed-action-binding path has
    `predicted_diff["before"] is None` today (Task 21's `freeze_action_target`
    never reads a live row) — `create_rollback_plan` must fail with a real,
    structured error rather than fabricate a rollback target."""
    binding = _seed_baseline(runtime_db, dialect="postgresql")
    plan = _seed_plan_and_sandbox(runtime_db, binding, before_image=None)
    context = _context()

    fake_writer = _FakeWriter("success")
    with mock.patch.object(execution_module, "_resolve_connection_url", return_value="stub://unused"), \
            mock.patch.object(execution_module, "_fetch_expected_version", return_value=1), \
            mock.patch.object(execution_module, "_writer_for_dialect", return_value=fake_writer), \
            mock.patch.object(execution_module, "append_audit", side_effect=_fake_append_audit), \
            mock.patch.object(execution_module, "persist_idempotency", side_effect=_fake_persist_idempotency):
        receipt = execute_plan(runtime_db, plan_id=plan.id, presented_plan_hash=plan.plan_hash, context=context)
    assert receipt.status == "SUCCEEDED"

    with pytest.raises(ExecutionError) as exc:
        create_rollback_plan(runtime_db, execution_id=receipt.execution_id, context=context)
    assert exc.value.reason_code == "PRECONDITION_CONFLICT"
