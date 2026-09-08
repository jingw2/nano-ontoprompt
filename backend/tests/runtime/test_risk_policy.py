"""Task 23: risk-based execution routing.

`evaluate_execution_policy` (`app.services.runtime.risk`) is a pure decision
function over an already-persisted `ActionPlan` (Task 15) and its
already-computed `SandboxResult` (Task 22) — it takes no database session,
so every fixture below is a hand-built dataclass literal, never a seeded
row. This suite proves:

- A plan bound to a concrete schema-pinned binding, scoped to exactly one
  resolved row, requiring no freshness grace, routes to `AUTOMATIC`.
- A plan carrying real risk (here: unbound, unscoped, multi-row impact)
  routes to `HUMAN_APPROVED` and carries the plan's own exact `plan_hash`
  as `required_plan_hash` for a human to present back through
  `approve_exact_plan`.
- Anything actually wrong at decision time — an expired plan, a snapshot
  that was already unknown-freshness when the plan was created, a plan
  whose own captured policy decision was never actually allowed, a Sandbox
  result that does not belong to this exact plan, or a Sandbox result whose
  recorded `plan_hash` no longer matches the plan's own — is REJECTED
  outright, never silently downgraded to a human-approved path.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.schemas.runtime import ReasonCode
from app.services.runtime.credentials import RuntimeContext, RuntimePrincipal
from app.services.runtime.risk import evaluate_execution_policy
from app.services.runtime.sandbox import SandboxResult
from app.services.runtime.service import ActionPlan

FIXED_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

AGENT_ID = "agent-risk-001"
USER_ID = "user-risk-001"

BASE_POLICY_DECISION = {
    "allowed": True,
    "reason_code": "ALLOW",
    "agent_capability": True,
    "user_entitlement": True,
    "freshness_state": "fresh",
    "freshness_lag_seconds": 30,
    "freshness_reason_code": "ALLOW",
    "requires_hitl": False,
}

BASE_PLAN_KWARGS = dict(
    id="plan-risk-001",
    semantic_snapshot_id="snap-risk-001",
    ontology_release_id="release-risk-001",
    agent_id=AGENT_ID,
    user_id=USER_ID,
    action_id="action-risk-001",
    input_facts={},
    evidence_citations=(),
    rule_outcomes=(),
    managed_action_binding_id="binding-risk-001",
    binding_version="1",
    parameters={"status": "approved"},
    target_key=("entity_type", "Target"),
    before_image_hash="b" * 64,
    version_hash="c" * 64,
    predicted_diff={"after": {"status": "approved"}},
    impact_scope={"instance_count": 1},
    risk_classification="single_instance_write",
    policy_decision=dict(BASE_POLICY_DECISION),
    precondition_hashes=("m" * 64, "s" * 64, "b" * 64),
    expiry=FIXED_NOW + timedelta(seconds=900),
    idempotency_key="idem-risk-001",
    plan_hash="p" * 64,
)


def _plan(**overrides) -> ActionPlan:
    kwargs = dict(BASE_PLAN_KWARGS)
    kwargs.update(overrides)
    return ActionPlan(**kwargs)


def _sandbox_for(plan: ActionPlan, **overrides) -> SandboxResult:
    base = dict(
        simulation_id="sim-risk-001",
        action_plan_id=plan.id,
        expected_rows=1,
        before_after_diff={"status": {"before": "pending", "after": "approved"}},
        impact_summary={"instance_count": 1},
        rule_outcome=(),
        policy_result=dict(plan.policy_decision),
        precondition_hashes={
            "plan_hash": plan.plan_hash,
            "before_image_hash": plan.before_image_hash,
            "version_hash": plan.version_hash,
            "materialization_hash": "m" * 64,
        },
        expires_at=plan.expiry,
    )
    base.update(overrides)
    return SandboxResult(**base)


def auto_plan() -> ActionPlan:
    return _plan()


def auto_sandbox() -> SandboxResult:
    return _sandbox_for(auto_plan())


def high_risk_plan() -> ActionPlan:
    return _plan(
        id="plan-risk-high-001",
        managed_action_binding_id=None,
        binding_version=None,
        risk_classification="unscoped_proposal",
        plan_hash="h" * 64,
    )


def high_risk_sandbox() -> SandboxResult:
    return _sandbox_for(high_risk_plan(), expected_rows=0, impact_summary={"instance_count": 0})


def valid_runtime_context() -> RuntimeContext:
    principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id=USER_ID, security_domain_id="domain-risk-001",
        audience="ontexus-runtime", scope=frozenset({"ontology:write"}), token_id="tok-risk-001",
    )
    return RuntimeContext(principal=principal, correlation_id="corr-risk-001")


def evaluate_fixture_risk(case_id: str):
    plan = auto_plan()
    context = valid_runtime_context()
    now = FIXED_NOW

    if case_id == "expired":
        plan = _plan(expiry=FIXED_NOW - timedelta(seconds=1))
        sandbox = _sandbox_for(plan)
    elif case_id == "stale-snapshot":
        plan = _plan(policy_decision={**BASE_POLICY_DECISION, "freshness_state": "unknown"})
        sandbox = _sandbox_for(plan)
    elif case_id == "policy-drift":
        plan = _plan(policy_decision={**BASE_POLICY_DECISION, "allowed": False})
        sandbox = _sandbox_for(plan)
    elif case_id == "sandbox-failed":
        sandbox = _sandbox_for(plan, action_plan_id="plan-risk-different")
    elif case_id == "hash-mismatch":
        base_sandbox = _sandbox_for(plan)
        sandbox = _sandbox_for(
            plan,
            precondition_hashes={**base_sandbox.precondition_hashes, "plan_hash": "z" * 64},
        )
    else:  # pragma: no cover - guards against a typo in the parametrize list
        raise ValueError(f"unknown case_id: {case_id}")

    return evaluate_execution_policy(plan, sandbox, context, now)


def test_low_risk_reversible_deterministic_plan_is_automatic():
    decision = evaluate_execution_policy(auto_plan(), auto_sandbox(), valid_runtime_context(), FIXED_NOW)
    assert decision.execution_class == "AUTOMATIC"
    assert decision.allowed


def test_high_risk_plan_requires_exact_hash_hitl():
    decision = evaluate_execution_policy(
        high_risk_plan(), high_risk_sandbox(), valid_runtime_context(), FIXED_NOW,
    )
    assert decision.execution_class == "HUMAN_APPROVED"
    assert decision.required_plan_hash == high_risk_plan().plan_hash
    # This case's risk factors (unbound_action, unscoped_target,
    # multi_row_impact) never include freshness_soft_stale — the plan's own
    # captured freshness_state is "fresh" with requires_hitl False — so this
    # must never be labeled ALLOW (that value means "nothing more to check,
    # safe to proceed as-is" per InvestigationResult's own invariant); it
    # gets its own dedicated reason code instead.
    assert decision.reason_code == ReasonCode.RISK_REQUIRES_APPROVAL.value


@pytest.mark.parametrize("case_id", ["expired", "stale-snapshot", "policy-drift", "sandbox-failed", "hash-mismatch"])
def test_invalid_plan_is_rejected(case_id):
    decision = evaluate_fixture_risk(case_id)
    assert decision.execution_class == "REJECTED"


def test_identity_mismatch_is_rejected():
    """A caller re-verified for a *different* Agent/user than the plan was
    proposed for must never be treated as the plan's own dual principal,
    even if that credential is independently valid on its own."""
    stranger = RuntimeContext(
        principal=RuntimePrincipal(
            agent_id="agent-someone-else", user_id="user-someone-else",
            security_domain_id="domain-risk-001", audience="ontexus-runtime",
            scope=frozenset({"ontology:write"}), token_id="tok-stranger-001",
        ),
        correlation_id="corr-stranger-001",
    )
    decision = evaluate_execution_policy(auto_plan(), auto_sandbox(), stranger, FIXED_NOW)
    assert decision.execution_class == "REJECTED"
    assert not decision.allowed


# --- approve_exact_plan (Task 23) -----------------------------------------
#
# Unlike `evaluate_execution_policy` above, `approve_exact_plan` is a
# database-backed operation: it loads a real, already-persisted
# `RuntimePlan` (Task 15) through `RuntimeService.get_action_plan` and a
# real `SandboxSimulation` (Task 22) through `simulate_action`, so this
# section seeds real rows through the same production code paths — never
# hand-built dataclasses — to prove the exact-hash/expiry/missing-Sandbox/
# execution-class gates hold against genuine plan/simulation artifacts.

import uuid

from app.models.action import Action as _Action
from app.models.oauth import OAuthClient as _OAuthClient
from app.models.ontology import OntologyProject as _OntologyProject
from app.models.ontology_data_grant import OntologyDataGrant as _OntologyDataGrant
from app.models.semantic_snapshot import SemanticSnapshot as _SemanticSnapshot
from app.models.user import User as _User
from app.models.v2.connection import Connection as _Connection
from app.services.actions.approval import ApprovalReceipt, approve_exact_plan
from app.services.runtime.action_bindings import _connection_identity as connection_identity
from app.services.runtime.action_bindings import publish_binding
from app.services.runtime.credentials import RuntimeAccessError
from app.services.runtime.sandbox import simulate_action
from app.services.runtime.service import ActionPlanRequest, RuntimeService

_APPROVAL_DOMAIN = "00000000-0000-0000-0000-0000000000aa"
_APPROVAL_CAPABILITIES = ["investigate", "propose_action"]
_APPROVAL_ONTOLOGY_ID = "ontology-approval-001"
_APPROVAL_RELEASE_ID = "release-approval-001"
_APPROVAL_SNAPSHOT_ID = "snap-approval-001"
_APPROVAL_AGENT_ID = "agent-approval-001"
_APPROVAL_USER_ID = "user-approval-001"
_APPROVAL_ACTION_ID = "action-approval-001"
_APPROVAL_CONNECTION_ID = "connection-approval-001"


def _seed_approval_baseline(db):
    user = _User(
        id=_APPROVAL_USER_ID, username=_APPROVAL_USER_ID, email=f"{_APPROVAL_USER_ID}@example.invalid",
        password_hash="not-a-real-password-hash", role="editor", security_domain_id=_APPROVAL_DOMAIN,
    )
    client = _OAuthClient(
        id=_APPROVAL_AGENT_ID, client_name="Approval Agent", redirect_uris=[], allowed_scopes=[],
        is_active=True, created_by=_APPROVAL_USER_ID, security_domain_id=_APPROVAL_DOMAIN,
        allowed_audiences=[], capability_names=list(_APPROVAL_CAPABILITIES),
    )
    db.add_all([user, client])
    db.commit()

    grant = _OntologyDataGrant(
        id=str(uuid.uuid4()), ontology_id=_APPROVAL_ONTOLOGY_ID, user_id=_APPROVAL_USER_ID,
        capabilities=list(_APPROVAL_CAPABILITIES), status="active", created_by=_APPROVAL_USER_ID,
    )
    db.add(grant)

    project = _OntologyProject(
        id=_APPROVAL_ONTOLOGY_ID, name="approval ontology", domain="test",
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
            "id": _APPROVAL_RELEASE_ID, "ontology_id": _APPROVAL_ONTOLOGY_ID, "manifest": b"approval-manifest",
            "schema_hash": hashlib.sha256(_APPROVAL_RELEASE_ID.encode()).digest(), "created_by": user.id,
        },
    )
    project.latest_published_release_id = _APPROVAL_RELEASE_ID
    db.commit()

    snapshot = _SemanticSnapshot(
        id=_APPROVAL_SNAPSHOT_ID, ontology_release_id=_APPROVAL_RELEASE_ID,
        quality_summary={"row_count": 1, "quality_score": 0.98}, evidence_summary={"citations": []},
        materialization_hash="a" * 64, status="materialized", created_by=user.id,
        freshness_state="fresh", freshness_lag_seconds=60,
        source_cursor={
            "source_id": "source-approval-001", "resource": "default",
            "contract": "watermark_primary_key", "watermark": None, "primary_key": "1",
            "opaque_value": None, "observed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    db.add(snapshot)
    db.add(_Action(id=_APPROVAL_ACTION_ID, ontology_id=_APPROVAL_ONTOLOGY_ID, name_cn="approve-target", enabled=True))
    db.commit()


def _approval_context() -> RuntimeContext:
    principal = RuntimePrincipal(
        agent_id=_APPROVAL_AGENT_ID, user_id=_APPROVAL_USER_ID, security_domain_id=_APPROVAL_DOMAIN,
        audience="ontexus-runtime", scope=frozenset({"ontology:read", "ontology:write"}),
        token_id="tok-approval-001",
    )
    return RuntimeContext(principal=principal, correlation_id="corr-approval-001")


def _create_unscoped_plan(db):
    """No target selector at all -> `risk_classification == "unscoped_proposal"`
    and no managed action binding -> `evaluate_execution_policy` routes this
    to HUMAN_APPROVED (see `test_high_risk_plan_requires_exact_hash_hitl`'s
    unit-level equivalent above)."""
    request = ActionPlanRequest(
        semantic_snapshot_id=_APPROVAL_SNAPSHOT_ID, action_id=_APPROVAL_ACTION_ID,
        parameters={"status": "approved"},
    )
    return RuntimeService().create_action_plan(request, _approval_context(), db)


def _create_bound_plan(db):
    """A schema-pinned, single-target managed action binding -> routes to
    AUTOMATIC — `approve_exact_plan` must never accept a hash for this
    plan, since it needs no human approval at all."""
    db.add(_Connection(id=_APPROVAL_CONNECTION_ID, name="approval-db", kind="postgres", status="active"))
    db.commit()
    # `publish_binding` computes the connection's own fingerprinted identity
    # and validates it matches — reusing it (rather than hand-constructing a
    # `ManagedActionBinding` row with a placeholder identity) is required for
    # `resolve_published_binding` to actually resolve this binding later
    # instead of failing closed with BINDING_DRIFT.
    binding = publish_binding(
        db, action_id=_APPROVAL_ACTION_ID, connection_id=_APPROVAL_CONNECTION_ID,
        connection_target_identity=connection_identity(db.get(_Connection, _APPROVAL_CONNECTION_ID)),
        dialect="postgresql", schema_name="public", table_name="targets",
        primary_key_columns=["target_id"], writable_columns=["status"], version_column="row_version",
        parameter_schema={"status": "string"}, secret_ref="vault://kv/connections/approval-db#password",
    )
    request = ActionPlanRequest(
        semantic_snapshot_id=_APPROVAL_SNAPSHOT_ID, action_id=_APPROVAL_ACTION_ID,
        parameters={"status": "approved"}, target_selector={"target_id": "TGT001"},
        managed_action_binding_id=binding.managed_action_binding_id, binding_version=binding.version,
    )
    return RuntimeService().create_action_plan(request, _approval_context(), db)


def test_approve_exact_plan_accepts_matching_hash_for_hitl_plan(db):
    _seed_approval_baseline(db)
    plan = _create_unscoped_plan(db)
    simulate_action(db, plan_id=plan.id, context=_approval_context())

    receipt = approve_exact_plan(
        db, plan_id=plan.id, presented_plan_hash=plan.plan_hash,
        approver_context=_approval_context(), now=datetime.now(timezone.utc),
    )
    assert isinstance(receipt, ApprovalReceipt)
    assert receipt.plan_id == plan.id
    assert receipt.plan_hash == plan.plan_hash
    assert receipt.execution_class == "HUMAN_APPROVED"


def test_approve_exact_plan_rejects_wrong_hash(db):
    _seed_approval_baseline(db)
    plan = _create_unscoped_plan(db)
    simulate_action(db, plan_id=plan.id, context=_approval_context())

    with pytest.raises(RuntimeAccessError) as exc:
        approve_exact_plan(
            db, plan_id=plan.id, presented_plan_hash="0" * 64,
            approver_context=_approval_context(), now=datetime.now(timezone.utc),
        )
    assert exc.value.reason_code == "INVALID_PLAN_HASH"


def test_approve_exact_plan_rejects_expired_plan(db):
    _seed_approval_baseline(db)
    plan = _create_unscoped_plan(db)
    simulate_action(db, plan_id=plan.id, context=_approval_context())

    with pytest.raises(RuntimeAccessError) as exc:
        approve_exact_plan(
            db, plan_id=plan.id, presented_plan_hash=plan.plan_hash,
            approver_context=_approval_context(), now=plan.expiry + timedelta(seconds=1),
        )
    assert exc.value.reason_code == "PLAN_EXPIRED"


def test_approve_exact_plan_rejects_plan_never_simulated(db):
    _seed_approval_baseline(db)
    plan = _create_unscoped_plan(db)
    # No `simulate_action` call: there is no Sandbox result to verify against.

    with pytest.raises(RuntimeAccessError) as exc:
        approve_exact_plan(
            db, plan_id=plan.id, presented_plan_hash=plan.plan_hash,
            approver_context=_approval_context(), now=datetime.now(timezone.utc),
        )
    assert exc.value.reason_code == "PRECONDITION_CONFLICT"


def test_approve_exact_plan_rejects_automatic_plan(db):
    """An AUTOMATIC plan needs no human approval at all — presenting its
    exact hash to `approve_exact_plan` must still be rejected, never
    silently accepted just because the hash matches."""
    _seed_approval_baseline(db)
    plan = _create_bound_plan(db)
    simulate_action(db, plan_id=plan.id, context=_approval_context())

    with pytest.raises(RuntimeAccessError) as exc:
        approve_exact_plan(
            db, plan_id=plan.id, presented_plan_hash=plan.plan_hash,
            approver_context=_approval_context(), now=datetime.now(timezone.utc),
        )
    assert exc.value.reason_code == "POLICY_DENIED"
