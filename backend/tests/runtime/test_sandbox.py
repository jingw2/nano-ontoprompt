"""Task 22: snapshot-backed Sandbox simulation.

`simulate_action` (`app.services.runtime.sandbox`) previews what an already
persisted, immutable `RuntimePlan` (Task 15) would do against its pinned
`SemanticSnapshot` and (when bound) its published `ManagedActionBinding`
(Task 21) — without ever touching a production connector, running arbitrary
SQL, or invoking prompt/tool/Agent execution. This suite proves:

- The happy path returns an immutable field-level before/after diff and
  real precondition hashes for a plan whose target already resolved to a
  live governed row.
- Nothing pending outside the one `SandboxSimulation` insert this function
  is allowed to make is ever committed — no connector call rides along.
- Every out-of-scope shape (a plan pinned to something other than a real
  materialized snapshot, a plan bound to a binding that is not currently
  published, and a plan whose action is not eligible for simulation at
  all — the "arbitrary SQL"/"prompt-tool-call" stand-ins for an action
  Sandbox has no safe, allowlisted surface to reason about) is rejected
  with a structured, closed-vocabulary reason code.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.action import Action
from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.managed_action import ManagedActionBinding
from app.models.oauth import OAuthClient
from app.models.ontology import OntologyProject
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.ontology_release import OntologyRelease
from app.models.runtime_plan import RuntimePlan
from app.models.sandbox import SandboxImmutableError, SandboxSimulation
from app.models.semantic_snapshot import SemanticSnapshot
from app.models.user import User
from app.models.v2.connection import Connection
from app.services.runtime.credentials import RuntimeContext, RuntimePrincipal
from app.services.runtime.sandbox import SandboxError, simulate_action

DOMAIN = "00000000-0000-0000-0000-0000000000cc"
CAPABILITIES = ["investigate", "propose_action"]
ONTOLOGY_ID = "ontology-sandbox-001"
RELEASE_ID = "release-sandbox-001"
SNAPSHOT_ID = "snap-sandbox-001"
AGENT_ID = "agent-sandbox-001"
USER_ID = "user-sandbox-001"
ACTION_ID = "action-sandbox-001"
CONNECTION_ID = "connection-sandbox-001"
PLAN_AUTO_001 = "plan-auto-001"


def _seed_user(db, *, user_id=USER_ID):
    user = db.get(User, user_id)
    if user is not None:
        return user
    user = User(
        id=user_id, username=user_id, email=f"{user_id}@example.invalid",
        password_hash="not-a-real-password-hash", role="editor", security_domain_id=DOMAIN,
    )
    db.add(user)
    db.commit()
    return user


def _seed_agent(db, *, agent_id=AGENT_ID):
    agent = db.get(OAuthClient, agent_id)
    if agent is not None:
        return agent
    client = OAuthClient(
        id=agent_id, client_name="Sandbox Agent", redirect_uris=[], allowed_scopes=[],
        is_active=True, created_by=USER_ID, security_domain_id=DOMAIN,
        allowed_audiences=[], capability_names=list(CAPABILITIES),
    )
    db.add(client)
    db.commit()
    return client


def _seed_grant(db, *, user_id=USER_ID):
    grant = OntologyDataGrant(
        id=str(uuid.uuid4()), ontology_id=ONTOLOGY_ID, user_id=user_id,
        capabilities=list(CAPABILITIES), status="active", created_by=user_id,
    )
    db.add(grant)
    db.commit()
    return grant


def _seed_release(db, user, *, release_id=RELEASE_ID):
    project = db.get(OntologyProject, ONTOLOGY_ID)
    if project is None:
        project = OntologyProject(
            id=ONTOLOGY_ID, name="sandbox ontology", domain="test",
            created_by=user.id, security_domain_id=user.security_domain_id,
        )
        db.add(project)
        db.flush()
    db.execute(
        text(
            "INSERT INTO ontology_releases "
            "(id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, status, created_by, created_at) "
            "VALUES (:id, :ontology_id, 1, 'v1', :manifest, "
            "'{\"entities\":[{\"id\":\"entity-sandbox-001\"}]}', :schema_hash, "
            "'published', :created_by, CURRENT_TIMESTAMP)"
        ),
        {
            "id": release_id, "ontology_id": ONTOLOGY_ID, "manifest": b"sandbox-manifest",
            "schema_hash": hashlib.sha256(release_id.encode()).digest(), "created_by": user.id,
        },
    )
    project.latest_published_release_id = release_id
    db.commit()
    return db.get(OntologyRelease, release_id)


def _seed_snapshot(db, release, user, *, snapshot_id=SNAPSHOT_ID):
    snapshot = db.get(SemanticSnapshot, snapshot_id)
    if snapshot is not None:
        return snapshot
    snapshot = SemanticSnapshot(
        id=snapshot_id, ontology_release_id=release.id,
        quality_summary={"row_count": 1, "quality_score": 0.98},
        evidence_summary={"citations": []},
        materialization_hash="a" * 64, status="materialized", created_by=user.id,
        freshness_state="fresh", freshness_lag_seconds=60,
        source_cursor={
            "source_id": "source-sandbox-001", "resource": "default",
            "contract": "watermark_primary_key", "watermark": None, "primary_key": "1",
            "opaque_value": None, "observed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    db.add(snapshot)
    db.commit()
    return snapshot


def _seed_action(db, *, action_id=ACTION_ID, enabled=True):
    action = db.get(Action, action_id)
    if action is not None:
        return action
    action = Action(id=action_id, ontology_id=ONTOLOGY_ID, name_cn="批准目标", enabled=enabled)
    db.add(action)
    db.commit()
    return action


def _seed_entity_instance(db, *, instance_id="inst-sandbox-001"):
    entity = db.get(Entity, "entity-sandbox-001")
    if entity is None:
        entity = Entity(id="entity-sandbox-001", ontology_id=ONTOLOGY_ID, name_cn="目标", name_en="Target")
        db.add(entity)
        db.flush()
    instance = EntityInstance(
        id=instance_id, entity_id=entity.id, ontology_id=ONTOLOGY_ID,
        row_identity="TGT001", row_data={"target_id": "TGT001", "status": "pending"}, revision=1,
    )
    db.add(instance)
    db.commit()
    return instance


def _seed_connection(db, *, connection_id=CONNECTION_ID):
    connection = db.get(Connection, connection_id)
    if connection is not None:
        return connection
    connection = Connection(id=connection_id, name="sandbox-db", kind="postgres", status="active")
    db.add(connection)
    db.commit()
    return connection


def _plan_kwargs(**overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=PLAN_AUTO_001,
        semantic_snapshot_id=SNAPSHOT_ID,
        ontology_release_id=RELEASE_ID,
        agent_id=AGENT_ID,
        user_id=USER_ID,
        action_id=ACTION_ID,
        input_facts={},
        evidence_citations=[],
        rule_outcomes=[{"rule_id": "action_eligibility", "result": "pass", "reason_code": "ALLOW"}],
        managed_action_binding_id=None,
        binding_version=None,
        parameters={"status": "approved"},
        target_key=[["entity_type", "Target"], ["instance_id", "inst-sandbox-001"]],
        before_image_hash="b" * 64,
        version_hash="c" * 64,
        predicted_diff={
            "target_key": [["entity_type", "Target"], ["instance_id", "inst-sandbox-001"]],
            "before": {"target_id": "TGT001", "status": "pending"},
            "after": {"status": "approved"},
        },
        impact_scope={"ontology_id": ONTOLOGY_ID, "action_id": ACTION_ID, "instance_count": 1},
        risk_classification="single_instance_write",
        policy_decision={"allowed": True, "reason_code": "ALLOW"},
        precondition_hashes=["a" * 64, "b" * 64, "c" * 64],
        expiry=now + timedelta(seconds=900),
        idempotency_key=f"idem-{uuid.uuid4()}",
        plan_hash="d" * 64,
        created_at=now,
    )
    defaults.update(overrides)
    return defaults


def _seed_plan(db, **overrides):
    plan = RuntimePlan(**_plan_kwargs(**overrides))
    db.add(plan)
    db.commit()
    return plan


@pytest.fixture(autouse=True)
def _governed_baseline(db):
    """Every test in this suite needs a fully governed snapshot/action and
    one concrete, already-resolved `RuntimePlan` ("plan-auto-001") to
    simulate — seeded here so the given test skeletons' exact signatures
    (`db, runtime_context[, production_spy]`) need no extra fixture params."""
    user = _seed_user(db)
    _seed_agent(db)
    _seed_grant(db)
    release = _seed_release(db, user)
    _seed_snapshot(db, release, user)
    _seed_action(db)
    _seed_entity_instance(db)
    _seed_plan(db)


@pytest.fixture
def runtime_context():
    principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id=USER_ID, security_domain_id=DOMAIN,
        audience="ontexus-runtime", scope=frozenset({"ontology:read", "ontology:write"}),
        token_id="tok-sandbox-001",
    )
    return RuntimeContext(principal=principal, correlation_id="corr-sandbox-001")


@pytest.fixture
def production_spy(db, monkeypatch):
    """Mirrors `tests/runtime/test_runtime_service.py`'s `production_spy`:
    wraps `db.commit()` on THIS session and records any pending insert/update
    that is not the one immutable `SandboxSimulation` row `simulate_action`
    is allowed to write — the load-bearing guard that no stray write (e.g. a
    connector call) ever rides along."""

    class _Spy:
        def __init__(self):
            self.calls: list = []

    spy = _Spy()
    original_commit = db.commit

    def guarded_commit(*args, **kwargs):
        pending = list(db.new) + list(db.dirty)
        spy.calls.extend(obj for obj in pending if not isinstance(obj, SandboxSimulation))
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(db, "commit", guarded_commit)
    return spy


def test_simulation_returns_immutable_diff_and_preconditions(db, runtime_context):
    result = simulate_action(db, plan_id=PLAN_AUTO_001, context=runtime_context)
    assert result.expected_rows == 1
    assert result.before_after_diff["status"] == {"before": "pending", "after": "approved"}
    assert result.precondition_hashes["version_hash"]

    stored = db.get(SandboxSimulation, result.simulation_id)
    assert stored is not None
    with pytest.raises(SandboxImmutableError):
        stored.status = "changed"
        db.commit()


def test_simulation_never_calls_production_connector(db, runtime_context, production_spy):
    simulate_action(db, plan_id=PLAN_AUTO_001, context=runtime_context)
    assert production_spy.calls == []


def _seed_release_only_case(db) -> str:
    """A plan pinned to a real *release* id where a materialized *snapshot*
    id belongs — mirrors `test_runtime_service.py`'s "release-only" request:
    no materialized snapshot governs it, so it can never be simulated."""
    plan_id = f"plan-{uuid.uuid4()}"
    _seed_plan(
        db, id=plan_id, semantic_snapshot_id=RELEASE_ID,
        managed_action_binding_id=None, binding_version=None,
        idempotency_key=f"idem-{plan_id}",
    )
    return plan_id


def _seed_draft_binding_case(db) -> str:
    """A plan bound to a managed action binding that is not (or no longer)
    `published` — Sandbox must never simulate against a draft/revoked
    binding's surface."""
    user = db.get(User, USER_ID)
    _seed_connection(db)
    binding_id = str(uuid.uuid4())
    binding = ManagedActionBinding(
        managed_action_binding_id=binding_id, action_id=ACTION_ID, version=1, status="draft",
        connection_id=CONNECTION_ID, connection_target_identity="postgres://sandbox-db#fingerprint",
        dialect="postgresql", schema_name="public", table_name="targets",
        primary_key_columns=["target_id"], writable_columns=["status"], version_column="row_version",
        parameter_schema={"status": "string"}, secret_ref="vault://kv/connections/sandbox-db#password",
    )
    db.add(binding)
    db.commit()

    plan_id = f"plan-{uuid.uuid4()}"
    _seed_plan(
        db, id=plan_id, managed_action_binding_id=binding_id, binding_version="1",
        idempotency_key=f"idem-{plan_id}",
    )
    return plan_id


def _seed_unsupported_action_case(db, *, flavor: str) -> str:
    """A plan whose action is not eligible for simulation at all — the
    stand-in for "arbitrary SQL" and "prompt/tool call" actions: Sandbox v1
    has no allowlisted surface to reason about what either would do, so both
    are represented the same mechanical way (a disabled `Action`) and
    rejected identically."""
    action_id = f"action-unsupported-{flavor}"
    _seed_action(db, action_id=action_id, enabled=False)
    plan_id = f"plan-{flavor}"
    _seed_plan(
        db, id=plan_id, action_id=action_id,
        managed_action_binding_id=None, binding_version=None,
        idempotency_key=f"idem-{plan_id}",
    )
    return plan_id


def simulate_fixture(db, runtime_context, case_id):
    if case_id == "release-only":
        plan_id = _seed_release_only_case(db)
    elif case_id == "draft-binding":
        plan_id = _seed_draft_binding_case(db)
    elif case_id in ("arbitrary-sql", "prompt-tool-call"):
        plan_id = _seed_unsupported_action_case(db, flavor=case_id)
    else:  # pragma: no cover - guards against a typo in the parametrize list
        raise ValueError(f"unknown case_id: {case_id}")
    return simulate_action(db, plan_id=plan_id, context=runtime_context)


@pytest.mark.parametrize("case_id", ["release-only", "draft-binding", "arbitrary-sql", "prompt-tool-call"])
def test_sandbox_boundary_rejects_out_of_scope_cases(db, runtime_context, case_id):
    with pytest.raises(SandboxError) as exc:
        simulate_fixture(db, runtime_context, case_id)
    assert exc.value.reason_code in {"SNAPSHOT_NOT_GOVERNED", "BINDING_DRIFT", "UNSUPPORTED_ACTION"}
