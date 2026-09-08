"""Task 26A: expose governed execution, reconciliation, and rollback through
Runtime REST.

Task 16 (`app.routers.v2.runtime`) already exposed `RuntimeService`'s
investigate/create_action_plan/get_action_plan through REST; Task 26 built
the governed execution capstone (`app.services.runtime.execution`), Task 22
built Sandbox (`app.services.runtime.sandbox`), and Task 26 also built
runtime reconciliation (`app.services.runtime.reconciliation`). This suite
proves the five new routes wire those already-reviewed services through the
SAME verified-credential dependency and structured-denial machinery Task 16
already established — no new policy or writer path is invented here.

`plan-auto-001`/`plan-hitl-001`/`execution-001`/`recon-001` are hand-seeded
directly against the ORM (mirroring `tests/runtime/test_execution_service.py`'s
own `_seed_plan_and_sandbox` convention) rather than built up through the
REST `POST /action-plans` endpoint, because that endpoint's own request body
(`ActionPlanRequestBody`) has no `managed_action_binding_id` field — a plan
proposed through it can therefore never be BOUND, and `execute_plan` refuses
to execute any unbound plan. Hand-seeding lets each fixture reach a real,
governed bound/HITL/executed state directly, exactly like the service-level
suite already does.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from sqlalchemy import text

from app.models.action import Action
from app.models.oauth import OAuthClient
from app.models.ontology import OntologyProject
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.ontology_release import OntologyRelease
from app.models.runtime_execution import RuntimeExecution, RuntimeReconciliationCase
from app.models.runtime_plan import RuntimePlan
from app.models.sandbox import SandboxSimulation
from app.models.semantic_snapshot import SemanticSnapshot
from app.models.user import User
from app.models.v2.connection import Connection
from app.services.runtime import execution as execution_module
from app.services.runtime.action_bindings import _connection_identity as connection_identity
from app.services.runtime.action_bindings import publish_binding
from app.services.runtime.credentials import issue_delegated_credential

AUDIENCE = "ontexus-runtime"
DOMAIN = "00000000-0000-0000-0000-0000000000ff"
ONTOLOGY_ID = "ontology-exec-api-001"
RELEASE_ID = "release-exec-api-001"
SNAPSHOT_ID = "snap-valid-001"
AGENT_ID = "agent-exec-api-001"
USER_ID = "user-exec-api-001"
ACTION_ID = "action-exec-api-001"
CONNECTION_ID = "connection-exec-api-001"
CAPABILITIES = ["investigate", "propose_action"]

PLAN_AUTO_ID = "plan-auto-001"
PLAN_HITL_ID = "plan-hitl-001"
PLAN_ROLLBACK_SOURCE_ID = "plan-rollback-source-001"
PLAN_RECON_ID = "plan-recon-001"
EXECUTION_ID = "execution-001"
EXECUTION_RECON_ID = "execution-recon-001"
RECON_ID = "recon-001"

# `RuntimePlan.plan_hash`/`before_image_hash`/`version_hash` are each a
# `String(64)` (the real column length any presented hash is compared
# against exactly, per `RuntimePlan`'s own `CheckConstraint`s) — these are
# fixed 64-character stand-ins, not the brief's illustrative short literal
# names, so the request bodies below can present the plan's OWN exact
# stored hash back to it, byte for byte.
PLAN_AUTO_HASH = "1" * 64
PLAN_HITL_HASH = "5" * 64
PLAN_ROLLBACK_SOURCE_HASH = "7" * 64
PLAN_RECON_HASH = "9" * 64
MATERIALIZATION_HASH = "a" * 64

_BASE_POLICY_DECISION = {
    "allowed": True, "reason_code": "ALLOW", "agent_capability": True, "user_entitlement": True,
    "freshness_state": "fresh", "freshness_lag_seconds": 30, "freshness_reason_code": "ALLOW",
    "requires_hitl": False,
}


def _seed_governed_state(db):
    """A fully governed snapshot plus one published `ManagedActionBinding` —
    everything the five new endpoints need to be exercised for real, through
    the same REST transport Task 16 already built."""
    user = User(
        id=USER_ID, username=USER_ID, email=f"{USER_ID}@example.invalid",
        password_hash="not-a-real-password-hash", role="editor", security_domain_id=DOMAIN,
    )
    db.add(user)
    db.commit()

    client = OAuthClient(
        id=AGENT_ID, client_name="Runtime Execution API Agent", redirect_uris=[],
        allowed_scopes=["ontology:read", "ontology:write"], is_active=True, created_by=USER_ID,
        security_domain_id=DOMAIN, allowed_audiences=[AUDIENCE], capability_names=list(CAPABILITIES),
    )
    db.add(client)
    db.add(OntologyDataGrant(
        id=str(uuid.uuid4()), ontology_id=ONTOLOGY_ID, user_id=USER_ID,
        capabilities=list(CAPABILITIES), status="active", created_by=USER_ID,
    ))
    db.commit()

    project = OntologyProject(
        id=ONTOLOGY_ID, name="exec api ontology", domain="test",
        created_by=user.id, security_domain_id=user.security_domain_id,
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
            "id": RELEASE_ID, "ontology_id": ONTOLOGY_ID, "manifest": b"exec-api-manifest",
            "schema_hash": hashlib.sha256(RELEASE_ID.encode()).digest(), "created_by": user.id,
        },
    )
    project.latest_published_release_id = RELEASE_ID
    db.commit()

    db.add(SemanticSnapshot(
        id=SNAPSHOT_ID, ontology_release_id=RELEASE_ID,
        quality_summary={"row_count": 1, "quality_score": 0.98}, evidence_summary={"citations": []},
        materialization_hash=MATERIALIZATION_HASH, status="materialized", created_by=user.id,
        freshness_state="fresh", freshness_lag_seconds=60,
        source_cursor={
            "source_id": "source-exec-api-001", "resource": "default", "contract": "watermark_primary_key",
            "watermark": None, "primary_key": "1", "opaque_value": None,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        },
    ))
    db.add(Action(id=ACTION_ID, ontology_id=ONTOLOGY_ID, name_cn="exec-api-target", enabled=True))
    db.add(Connection(id=CONNECTION_ID, name="exec-api-db", kind="postgres", status="active"))
    db.commit()

    return publish_binding(
        db, action_id=ACTION_ID, connection_id=CONNECTION_ID,
        connection_target_identity=connection_identity(db.get(Connection, CONNECTION_ID)),
        dialect="postgresql", schema_name="public", table_name="managed_targets",
        primary_key_columns=["target_id"], writable_columns=["status"], version_column="row_version",
        parameter_schema={"status": "string"}, secret_ref="vault://kv/connections/exec-api-db#password",
    )


def _seed_plan(db, *, plan_id: str, plan_hash: str, target_id: str, binding=None, policy_overrides=None,
               managed: bool = True, target_key_override=None, before_image=None, parameters=None):
    now = datetime.now(timezone.utc)
    parameters = parameters if parameters is not None else {"status": "confirmed"}
    target_key = target_key_override if target_key_override is not None else [["target_id", target_id]]
    policy_decision = {**_BASE_POLICY_DECISION, **(policy_overrides or {})}
    row = RuntimePlan(
        id=plan_id, semantic_snapshot_id=SNAPSHOT_ID, ontology_release_id=RELEASE_ID,
        agent_id=AGENT_ID, user_id=USER_ID, action_id=ACTION_ID, input_facts={}, evidence_citations=[],
        rule_outcomes=[{"rule_id": "action_eligibility", "result": "pass", "reason_code": "ALLOW"}],
        managed_action_binding_id=binding.managed_action_binding_id if (managed and binding) else None,
        binding_version=str(binding.version) if (managed and binding) else None,
        parameters=parameters, target_key=target_key,
        before_image_hash=hashlib.sha256(f"before:{plan_id}".encode()).hexdigest(),
        version_hash=hashlib.sha256(f"version:{plan_id}".encode()).hexdigest(),
        predicted_diff={"target_key": target_key, "before": before_image, "after": dict(parameters)},
        impact_scope={"ontology_id": ONTOLOGY_ID, "action_id": ACTION_ID, "instance_count": 1},
        risk_classification="single_instance_write",
        policy_decision=policy_decision,
        precondition_hashes=[MATERIALIZATION_HASH, "s" * 64, "b" * 64],
        expiry=now + timedelta(seconds=900),
        idempotency_key=f"idem-{plan_id}",
        plan_hash=plan_hash,
        created_at=now,
    )
    db.add(row)
    db.commit()
    return row


def _seed_sandbox(db, plan: RuntimePlan):
    db.add(SandboxSimulation(
        id=str(uuid.uuid4()), action_plan_id=plan.id, semantic_snapshot_id=SNAPSHOT_ID,
        ontology_release_id=RELEASE_ID, agent_id=AGENT_ID, user_id=USER_ID,
        managed_action_binding_id=plan.managed_action_binding_id, binding_version=plan.binding_version,
        expected_rows=1, before_after_diff={"status": {"before": None, "after": "approved"}},
        impact_summary={"instance_count": 1}, rule_outcome=[], policy_result=dict(plan.policy_decision),
        expires_at=plan.expiry,
        precondition_hashes={
            "plan_hash": plan.plan_hash, "before_image_hash": plan.before_image_hash,
            "version_hash": plan.version_hash, "materialization_hash": MATERIALIZATION_HASH,
        },
        status="simulated",
    ))
    db.commit()


@pytest.fixture
def governed_state(db):
    binding = _seed_governed_state(db)

    # `plan-auto-001`: bound, no risk factors -> AUTOMATIC. Only exercised
    # by the sandbox route in this suite (no pre-seeded SandboxSimulation
    # needed — `simulate_action` computes and persists its own).
    _seed_plan(db, plan_id=PLAN_AUTO_ID, plan_hash=PLAN_AUTO_HASH, target_id="target-auto-001", binding=binding)

    # `plan-hitl-001`: bound, but soft-stale at plan-creation time
    # (`requires_hitl=True`) -> routes to HUMAN_APPROVED via risk.py's
    # `freshness_soft_stale` factor. A SandboxSimulation is pre-seeded
    # (mirroring `test_execution_service.py`) since both `approve_exact_plan`
    # and `execute_plan` require one to already exist.
    hitl_plan = _seed_plan(
        db, plan_id=PLAN_HITL_ID, plan_hash=PLAN_HITL_HASH, target_id="target-hitl-001", binding=binding,
        policy_overrides={"freshness_state": "stale", "requires_hitl": True},
    )
    _seed_sandbox(db, hitl_plan)

    # An UNBOUND plan with a real retained before-image — the one shape
    # `create_rollback_plan`'s success path can actually restore (every
    # BOUND plan's `predicted_diff["before"]` is `None` by construction, see
    # `execution.py`'s own docstring) — plus a hand-seeded `RuntimeExecution`
    # row pointing at it, standing in for a plan that was actually executed.
    rollback_source = _seed_plan(
        db, plan_id=PLAN_ROLLBACK_SOURCE_ID, plan_hash=PLAN_ROLLBACK_SOURCE_HASH,
        target_id="unused", binding=None, managed=False,
        target_key_override=[["instance_id", "inst-rollback-001"]],
        before_image={"status": "old_value"}, parameters={"status": "confirmed"},
    )
    db.add(RuntimeExecution(
        id=EXECUTION_ID, plan_id=rollback_source.id, plan_hash=rollback_source.plan_hash,
        execution_class="AUTOMATIC", dialect="postgresql", status="SUCCEEDED",
        writer_receipt={"affected_rows": 1}, audit_id=f"audit-{uuid.uuid4()}",
        idempotency_key=rollback_source.idempotency_key, correlation_id="corr-seeded-001",
    ))
    db.commit()

    # A second plan/execution pair backing a hand-seeded, already-open
    # reconciliation case — reconciliation cases are opened only for
    # UNKNOWN execution outcomes (see `execution.py`), so this stands in for
    # that real shape without needing a live writer/connection.
    recon_plan = _seed_plan(
        db, plan_id=PLAN_RECON_ID, plan_hash=PLAN_RECON_HASH, target_id="target-recon-001", binding=binding,
    )
    db.add(RuntimeExecution(
        id=EXECUTION_RECON_ID, plan_id=recon_plan.id, plan_hash=recon_plan.plan_hash,
        execution_class="AUTOMATIC", dialect="postgresql", status="UNKNOWN",
        idempotency_key=recon_plan.idempotency_key, correlation_id="corr-seeded-002",
    ))
    db.commit()
    db.add(RuntimeReconciliationCase(
        id=RECON_ID, execution_id=EXECUTION_RECON_ID, plan_id=recon_plan.id, status="open",
        unknown_reason="simulated network partition — commit outcome unknown",
        observed_effect={"dialect": "postgresql", "managed_action_binding_id": binding.managed_action_binding_id},
        next_action="human_review",
    ))
    db.commit()

    return db


def _issue_token(db, *, scope: set[str]) -> str:
    return issue_delegated_credential(
        db, client_id=AGENT_ID, user_id=USER_ID, audience=AUDIENCE,
        scope=scope, ttl_seconds=300, now=datetime.now(timezone.utc),
    )


@pytest.fixture
def runtime_headers(governed_state):
    token = _issue_token(governed_state, scope={"ontology:read", "ontology:write"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def missing_scope_headers(governed_state):
    # A credential issued without "ontology:write" — the scope every
    # mutating Phase 3 route (approve/execute/rollback-plans) requires. A
    # credential-layer denial (SCOPE_DENIED), verified before any of
    # `record_plan_approval`/`execute_plan`/`create_rollback_plan` ever runs.
    token = _issue_token(governed_state, scope={"ontology:read"})
    return {"Authorization": f"Bearer {token}"}


def test_sandbox_query_requires_verified_context_and_returns_diff(client, runtime_headers):
    response = client.get(
        f"/api/v2/runtime/action-plans/{PLAN_AUTO_ID}/sandbox",
        headers=runtime_headers,
    )
    assert response.status_code == 200
    assert response.json()["action_plan_id"] == PLAN_AUTO_ID
    assert response.json()["semantic_snapshot_id"] == SNAPSHOT_ID


def test_sandbox_query_missing_credential_is_401(client, governed_state):
    response = client.get(f"/api/v2/runtime/action-plans/{PLAN_AUTO_ID}/sandbox")
    assert response.status_code == 401
    assert response.json()["decision"] == "DENY"


def test_sandbox_query_denies_unknown_plan(client, runtime_headers):
    response = client.get("/api/v2/runtime/action-plans/no-such-plan/sandbox", headers=runtime_headers)
    assert response.status_code == 403
    assert response.json()["decision"] == "DENY"
    assert response.json()["result"] is None


def test_approve_execute_and_rollback_accept_only_exact_hash_or_execution_id(client, runtime_headers):
    approve = client.post(
        f"/api/v2/runtime/action-plans/{PLAN_HITL_ID}/approve",
        json={"plan_hash": PLAN_HITL_HASH},
        headers=runtime_headers,
    )
    # `persist_idempotency`'s real implementation (`app.services.idempotency`)
    # writes with a raw SQL `now()` call — PostgreSQL-only, unsupported by
    # the SQLite unit-test harness (`tests/runtime/test_execution_service.py`
    # mocks this same function for every one of its own real-write-path
    # tests, for the identical reason). This is the one call inside
    # `execute_plan`'s execution-fence step that needs a stand-in here; every
    # governance gate before and after it still runs for real.
    with mock.patch.object(execution_module, "persist_idempotency", return_value="stored"):
        execute = client.post(
            f"/api/v2/runtime/action-plans/{PLAN_HITL_ID}/execute",
            json={"plan_hash": PLAN_HITL_HASH},
            headers=runtime_headers,
        )
    rollback = client.post(
        f"/api/v2/runtime/executions/{EXECUTION_ID}/rollback-plans",
        json={},
        headers=runtime_headers,
    )
    assert approve.status_code == 200
    assert approve.json()["plan_id"] == PLAN_HITL_ID
    assert approve.json()["execution_class"] == "HUMAN_APPROVED"

    assert execute.status_code == 200
    # No `RUNTIME_POSTGRES_URL` is configured in this unit-test process, so
    # the actual writer call fails at the final, post-fence step — but every
    # governance gate before it (hash, expiry, binding, sandbox linkage,
    # recorded approval, snapshot/policy re-check, override rejection) had
    # to pass first, or `execute_plan` would have raised before ever
    # reaching the fence and this route would have returned 403, not 200.
    assert execute.json()["plan_id"] == PLAN_HITL_ID

    assert rollback.status_code == 201
    assert rollback.json()["action_id"] == ACTION_ID
    assert rollback.json()["parameters"] == {"status": "old_value"}


def test_execute_rejects_a_hash_that_does_not_match_the_plan(client, runtime_headers):
    response = client.post(
        f"/api/v2/runtime/action-plans/{PLAN_HITL_ID}/execute",
        json={"plan_hash": "0" * 64},
        headers=runtime_headers,
    )
    assert response.status_code == 403
    assert response.json()["decision"] == "DENY"
    assert response.json()["reason_code"] == "INVALID_PLAN_HASH"


def test_execute_rejects_body_fields_beyond_plan_hash(client, runtime_headers):
    response = client.post(
        f"/api/v2/runtime/action-plans/{PLAN_HITL_ID}/execute",
        json={"plan_hash": PLAN_HITL_HASH, "parameters": {"status": "hacked"}},
        headers=runtime_headers,
    )
    assert response.status_code == 422


def test_approve_without_write_scope_is_denied(client, missing_scope_headers):
    response = client.post(
        f"/api/v2/runtime/action-plans/{PLAN_HITL_ID}/approve",
        json={"plan_hash": PLAN_HITL_HASH},
        headers=missing_scope_headers,
    )
    assert response.status_code == 403
    assert response.json()["decision"] == "DENY"
    assert response.json()["reason_code"] == "SCOPE_DENIED"


def test_reconciliation_query_and_denial_are_structured(client, runtime_headers, missing_scope_headers):
    allowed = client.get(
        f"/api/v2/runtime/reconciliations/{RECON_ID}",
        headers=runtime_headers,
    )
    denied = client.post(
        f"/api/v2/runtime/action-plans/{PLAN_HITL_ID}/execute",
        json={"plan_hash": "0" * 64},
        headers=missing_scope_headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "UNKNOWN"
    assert allowed.json()["plan_id"] == PLAN_RECON_ID
    assert "secret" not in str(allowed.json()).lower()

    assert denied.status_code == 403
    assert denied.json()["decision"] == "DENY"
    assert denied.json()["reason_code"] in {"SCOPE_DENIED", "INVALID_PLAN_HASH"}
    assert denied.json().get("result") is None


def test_reconciliation_query_denies_unknown_case(client, runtime_headers):
    response = client.get("/api/v2/runtime/reconciliations/no-such-case", headers=runtime_headers)
    assert response.status_code == 403
    assert response.json()["decision"] == "DENY"


def test_reconciliation_query_denies_a_different_principal(client, governed_state):
    # A stranger credential for an unrelated Agent/user pair must never read
    # a reconciliation case it does not own, even by guessing a valid id.
    stranger = User(
        id="user-stranger-001", username="user-stranger-001", email="stranger@example.invalid",
        password_hash="not-a-real-password-hash", role="editor", security_domain_id=DOMAIN,
    )
    governed_state.add(stranger)
    governed_state.add(OAuthClient(
        id="agent-stranger-001", client_name="Stranger Agent", redirect_uris=[],
        allowed_scopes=["ontology:read", "ontology:write"], is_active=True, created_by="user-stranger-001",
        security_domain_id=DOMAIN, allowed_audiences=[AUDIENCE], capability_names=list(CAPABILITIES),
    ))
    governed_state.commit()
    stranger_token = issue_delegated_credential(
        governed_state, client_id="agent-stranger-001", user_id="user-stranger-001", audience=AUDIENCE,
        scope={"ontology:read", "ontology:write"}, ttl_seconds=300, now=datetime.now(timezone.utc),
    )
    response = client.get(
        f"/api/v2/runtime/reconciliations/{RECON_ID}",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )
    assert response.status_code == 403
    assert response.json()["decision"] == "DENY"


def test_rollback_plan_denies_unknown_execution(client, runtime_headers):
    response = client.post(
        "/api/v2/runtime/executions/no-such-execution/rollback-plans", json={}, headers=runtime_headers,
    )
    assert response.status_code == 403
    assert response.json()["decision"] == "DENY"


def test_execution_status_reflects_a_real_execution_after_execute(client, runtime_headers):
    """Task 26A's optional fix: `GET /execution-status/{plan_id}` now wires
    to the real `get_execution_status` instead of always reporting
    `not_started` — proven here against a plan that really has an
    executed/executing record via the seeded `execution-001` fixture."""
    response = client.get(
        f"/api/v2/runtime/execution-status/{PLAN_ROLLBACK_SOURCE_ID}", headers=runtime_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"] == PLAN_ROLLBACK_SOURCE_ID
    assert body["status"] == "SUCCEEDED"
    assert body["execution_id"] == EXECUTION_ID
