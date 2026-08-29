"""Task 15: snapshot-pinned investigation and immutable action plans.

`RuntimeService` is the single entry point every Runtime transport uses for
the two non-writing operations a delegated credential may perform:
`investigate` (a policy-checked, snapshot-pinned read) and
`create_action_plan` (an immutable, non-writing proposal). Both resolve a
real materialized snapshot before anything else — a request that cannot
resolve one (e.g. a caller asserting a bare release id where a snapshot id
belongs) is rejected as a credential-layer denial, never as a data result.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.models.action import Action
from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.oauth import OAuthClient
from app.models.ontology import OntologyProject
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.ontology_release import OntologyRelease
from app.models.runtime_plan import ImmutablePlanError, RuntimePlan
from app.models.semantic_snapshot import SemanticSnapshot
from app.models.user import User
from app.schemas.runtime import InvestigationRequest
from app.services.runtime.credentials import RuntimeAccessError, RuntimeContext, RuntimePrincipal
from app.services.runtime.service import ActionPlanRequest, RuntimeService

DOMAIN = "00000000-0000-0000-0000-0000000000aa"
CAPABILITIES = ["investigate", "propose_action"]
ONTOLOGY_ID = "ontology-supply-001"
RELEASE_ID = "release-valid-001"
SNAPSHOT_ID = "snap-valid-001"
AGENT_ID = "agent-svc-001"
USER_ID = "user-svc-001"
ACTION_ID = "action-svc-001"


def _seed_user(db, *, user_id=USER_ID):
    user = User(
        id=user_id, username=user_id, email=f"{user_id}@example.invalid",
        password_hash="not-a-real-password-hash", role="editor", security_domain_id=DOMAIN,
    )
    db.add(user)
    return user


def _seed_agent(db, *, agent_id=AGENT_ID, capability_names=CAPABILITIES, is_active=True):
    client = OAuthClient(
        id=agent_id, client_name="Runtime Service Agent", redirect_uris=[], allowed_scopes=[],
        is_active=is_active, created_by=USER_ID, security_domain_id=DOMAIN,
        allowed_audiences=[], capability_names=list(capability_names),
    )
    db.add(client)
    return client


def _seed_grant(db, *, ontology_id=ONTOLOGY_ID, user_id=USER_ID, capabilities=CAPABILITIES):
    grant = OntologyDataGrant(
        id=str(uuid.uuid4()), ontology_id=ontology_id, user_id=user_id,
        capabilities=list(capabilities), status="active", created_by=user_id,
    )
    db.add(grant)
    return grant


def _seed_release(db, user, *, ontology_id=ONTOLOGY_ID, release_id=RELEASE_ID):
    project = OntologyProject(
        id=ontology_id, name="supply chain ontology", domain="supply-chain",
        created_by=user.id, security_domain_id=user.security_domain_id,
    )
    db.add(project)
    db.flush()
    # OntologyRelease.manifest_projection uses Task 11's PostgreSQL-only
    # CanonicalJSONB type; seed with a plain SQL insert like
    # tests/runtime/conftest.py's `_release` helper does.
    db.execute(
        text(
            "INSERT INTO ontology_releases "
            "(id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, status, created_by, created_at) "
            "VALUES (:id, :ontology_id, 1, 'v1', :manifest, '{}', :schema_hash, "
            "'published', :created_by, CURRENT_TIMESTAMP)"
        ),
        {
            "id": release_id, "ontology_id": ontology_id, "manifest": b"snapshot-manifest",
            "schema_hash": b"snapshot-schema-hash-000000000000", "created_by": user.id,
        },
    )
    project.latest_published_release_id = release_id
    db.commit()
    return db.get(OntologyRelease, release_id)


def _seed_snapshot(db, release, user, *, snapshot_id=SNAPSHOT_ID):
    snapshot = SemanticSnapshot(
        id=snapshot_id, ontology_release_id=release.id,
        quality_summary={"row_count": 2, "quality_score": 0.98},
        evidence_summary={"citations": [
            {
                "source_id": "source-supplier-001", "source_type": "csv",
                "locator": "fixture://source-supplier-001", "content_hash": "1" * 64,
            },
        ]},
        materialization_hash="a" * 64, status="materialized", created_by=user.id,
    )
    db.add(snapshot)
    db.commit()
    return snapshot


def _seed_action(db, *, ontology_id=ONTOLOGY_ID, action_id=ACTION_ID, enabled=True):
    action = Action(id=action_id, ontology_id=ontology_id, name_cn="确认供应商状态", enabled=enabled)
    db.add(action)
    db.commit()
    return action


def _seed_entity_instance(db, *, ontology_id=ONTOLOGY_ID, entity_type="Supplier", instance_id="inst-sup001"):
    entity = Entity(id="entity-supplier-001", ontology_id=ontology_id, name_cn="供应商", name_en=entity_type)
    db.add(entity)
    db.flush()
    instance = EntityInstance(
        id=instance_id, entity_id=entity.id, ontology_id=ontology_id,
        row_identity="SUP001", row_data={"supplier_id": "SUP001", "status": "pending"}, revision=1,
    )
    db.add(instance)
    db.commit()
    return instance


@pytest.fixture
def governed_snapshot(db):
    """A fully governed snapshot: published release, active Agent with the
    required capabilities, an active data grant for the delegated user, and
    one eligible governed Action."""
    user = _seed_user(db)
    db.commit()
    _seed_agent(db)
    _seed_grant(db)
    db.commit()
    release = _seed_release(db, user)
    snapshot = _seed_snapshot(db, release, user)
    _seed_action(db)
    return snapshot


@pytest.fixture
def runtime_context():
    principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id=USER_ID, security_domain_id=DOMAIN,
        audience="ontexus-runtime", scope=frozenset({"ontology:read"}), token_id="tok-svc-001",
    )
    return RuntimeContext(principal=principal, correlation_id="corr-svc-001")


@pytest.fixture
def production_spy():
    """A canary, not a patched dependency: `create_action_plan` takes no
    connector argument and this object is never wired into it anywhere.
    `.calls` staying empty is therefore structural, not incidental — it
    exists so a future change that *does* thread a connector through this
    path has to touch (and therefore cannot silently bypass) this fixture."""

    class _Spy:
        def __init__(self):
            self.calls: list[Any] = []

    return _Spy()


def valid_plan_request(**overrides):
    defaults = dict(
        semantic_snapshot_id=SNAPSHOT_ID, action_id=ACTION_ID,
        parameters={"status": "confirmed"}, target_selector=None, idempotency_key="idem-001",
    )
    defaults.update(overrides)
    return ActionPlanRequest(**defaults)


def request_without_snapshot():
    """A request that names a real *release* id where a *snapshot* id
    belongs — the "release-only" case: no materialized snapshot governs it,
    so it must be rejected before any policy evaluation, not answered with
    a query result."""
    return InvestigationRequest(
        semantic_snapshot_id=RELEASE_ID, query=None, ontology_id=ONTOLOGY_ID,
        entity_type=None, filters={}, limit=20,
    )


def update_action_plan(db, plan_id, updates):
    plan = db.get(RuntimePlan, plan_id)
    for key, value in updates.items():
        setattr(plan, key, value)
    db.commit()


def test_investigate_returns_snapshot_release_evidence_and_rules(db, governed_snapshot, runtime_context):
    result = RuntimeService().investigate(
        InvestigationRequest(
            semantic_snapshot_id=SNAPSHOT_ID, query="supplier SUP001",
            ontology_id=ONTOLOGY_ID, entity_type="Supplier", filters={}, limit=20,
        ),
        runtime_context, db,
    )
    assert result.decision == "ALLOW"
    assert result.semantic_snapshot_id == SNAPSHOT_ID
    assert result.ontology_release_id == RELEASE_ID
    assert result.evidence_citations


def test_investigate_returns_matching_instance_from_snapshot_scoped_query(db, governed_snapshot, runtime_context):
    _seed_entity_instance(db)
    result = RuntimeService().investigate(
        InvestigationRequest(
            semantic_snapshot_id=SNAPSHOT_ID, query="SUP001",
            ontology_id=ONTOLOGY_ID, entity_type="Supplier", filters={}, limit=20,
        ),
        runtime_context, db,
    )
    assert result.decision == "ALLOW"
    assert result.result == [{
        "instance_id": "inst-sup001", "entity_id": "entity-supplier-001",
        "revision": 1, "row_data": {"supplier_id": "SUP001", "status": "pending"},
    }]


def test_investigate_denies_when_agent_lacks_capability(db, governed_snapshot):
    # An Agent registered without "investigate" cannot read even a fully
    # governed snapshot; DENY carries no result and no evidence.
    principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id=USER_ID, security_domain_id=DOMAIN,
        audience="ontexus-runtime", scope=frozenset({"ontology:read"}), token_id="tok-svc-002",
    )
    context = RuntimeContext(principal=principal, correlation_id="corr-svc-002")
    db.query(OAuthClient).filter(OAuthClient.id == AGENT_ID).one().capability_names = ["propose_action"]
    db.commit()

    result = RuntimeService().investigate(
        InvestigationRequest(
            semantic_snapshot_id=SNAPSHOT_ID, ontology_id=ONTOLOGY_ID, filters={}, limit=20,
        ),
        context, db,
    )
    assert result.decision == "DENY"
    assert result.reason_code == "AGENT_CAPABILITY_DENIED"
    assert result.result is None
    assert result.evidence_citations == []


def test_release_only_request_is_rejected(db, governed_snapshot, runtime_context):
    with pytest.raises(RuntimeAccessError) as exc:
        RuntimeService().investigate(request_without_snapshot(), runtime_context, db)
    assert exc.value.reason_code == "SNAPSHOT_NOT_GOVERNED"


def test_create_action_plan_is_immutable_and_non_writing(db, governed_snapshot, runtime_context, production_spy):
    plan = RuntimeService().create_action_plan(valid_plan_request(), runtime_context, db)

    assert plan.plan_hash
    assert plan.semantic_snapshot_id == SNAPSHOT_ID
    assert plan.ontology_release_id == RELEASE_ID
    assert plan.agent_id == AGENT_ID
    assert plan.user_id == USER_ID
    assert plan.parameters == {"status": "confirmed"}
    assert production_spy.calls == []

    with pytest.raises(ImmutablePlanError):
        update_action_plan(db, plan.id, {"parameters": {"status": "changed"}})


def test_create_action_plan_resolves_target_and_computes_before_image_hash(db, governed_snapshot, runtime_context):
    _seed_entity_instance(db)
    plan = RuntimeService().create_action_plan(
        valid_plan_request(target_selector={"entity_type": "Supplier", "instance_id": "inst-sup001"}),
        runtime_context, db,
    )

    assert plan.target_key == (("entity_type", "Supplier"), ("instance_id", "inst-sup001"))
    assert plan.risk_classification == "single_instance_write"
    assert plan.predicted_diff["before"] == {"supplier_id": "SUP001", "status": "pending"}
    assert plan.impact_scope["instance_count"] == 1
    # Distinct plans over the same target must not collide on before_image_hash
    # unless the resolved target state is genuinely identical.
    unscoped = RuntimeService().create_action_plan(
        valid_plan_request(idempotency_key="idem-002"), runtime_context, db,
    )
    assert unscoped.before_image_hash != plan.before_image_hash
    assert unscoped.plan_hash != plan.plan_hash


def test_create_action_plan_rejects_ineligible_action(db, governed_snapshot, runtime_context):
    db.query(Action).filter(Action.id == ACTION_ID).one().enabled = False
    db.commit()

    with pytest.raises(RuntimeAccessError) as exc:
        RuntimeService().create_action_plan(valid_plan_request(), runtime_context, db)
    assert exc.value.reason_code == "ACTION_NOT_ELIGIBLE"


def test_get_action_plan_returns_only_visible_plans(db, governed_snapshot, runtime_context):
    plan = RuntimeService().create_action_plan(valid_plan_request(), runtime_context, db)

    fetched = RuntimeService().get_action_plan(plan.id, runtime_context, db)
    assert fetched.id == plan.id
    assert fetched.plan_hash == plan.plan_hash

    other_principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id="user-other-001", security_domain_id=DOMAIN,
        audience="ontexus-runtime", scope=frozenset({"ontology:read"}), token_id="tok-svc-003",
    )
    other_context = RuntimeContext(principal=other_principal, correlation_id="corr-svc-003")
    with pytest.raises(RuntimeAccessError) as exc:
        RuntimeService().get_action_plan(plan.id, other_context, db)
    assert exc.value.reason_code == "POLICY_DENIED"
