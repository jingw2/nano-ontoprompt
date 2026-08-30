"""Task 16: expose the shared Runtime through versioned REST endpoints.

`RuntimeService` (Task 15) is the single place every Runtime transport calls
for investigation and action-plan proposals. These tests exercise the REST
transport adapter (`app.routers.v2.runtime`) end to end through the real
FastAPI test client: a real delegated credential is issued and presented as
a bearer token, so verification, policy evaluation, and result mapping all
run for real — nothing here calls `RuntimeService` directly.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.action import Action
from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.oauth import OAuthClient
from app.models.ontology import OntologyProject
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.ontology_release import OntologyRelease
from app.models.semantic_snapshot import SemanticSnapshot
from app.models.user import User
from app.services.runtime.canonical import compute_plan_hash, normalize_investigation
from app.services.runtime.credentials import issue_delegated_credential
from app.services.auth_service import create_access_token

AUDIENCE = "ontexus-runtime"
DOMAIN = "00000000-0000-0000-0000-0000000000cc"
ONTOLOGY_ID = "ontology-supply-001"
RELEASE_ID = "release-valid-001"
SNAPSHOT_ID = "snap-valid-001"
AGENT_ID = "agent-api-001"
USER_ID = "user-api-001"
ACTION_ID = "action-api-001"
CAPABILITIES = ["investigate", "propose_action"]
MANIFEST_PROJECTION = '{"entities":[{"id":"entity-supplier-api-001"}]}'


def _seed_governed_state(db):
    """A fully governed snapshot reachable through the REST transport: a
    published release, an active Agent with both Runtime capabilities, an
    active data grant for the delegated user, and one eligible Action."""
    user = User(
        id=USER_ID, username=USER_ID, email=f"{USER_ID}@example.invalid",
        password_hash="not-a-real-password-hash", role="editor", security_domain_id=DOMAIN,
    )
    db.add(user)
    db.commit()

    client = OAuthClient(
        id=AGENT_ID, client_name="Runtime API Agent", redirect_uris=[],
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
        id=ONTOLOGY_ID, name="supply chain ontology", domain="supply-chain",
        created_by=user.id, security_domain_id=user.security_domain_id,
    )
    db.add(project)
    db.flush()
    # OntologyRelease.manifest_projection uses Task 11's PostgreSQL-only
    # CanonicalJSONB type; the shared SQLite unit harness cannot run the
    # PostgreSQL cast expression, so seed with a plain SQL insert (mirrors
    # tests/runtime/test_runtime_service.py's `_seed_release` helper).
    db.execute(
        text(
            "INSERT INTO ontology_releases "
            "(id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, status, created_by, created_at) "
            "VALUES (:id, :ontology_id, :version_no, :version, :manifest, "
            ":manifest_projection, :schema_hash, 'published', :created_by, CURRENT_TIMESTAMP)"
        ),
        {
            "id": RELEASE_ID, "ontology_id": ONTOLOGY_ID, "version_no": 1, "version": "v1",
            "manifest": b"snapshot-manifest", "manifest_projection": MANIFEST_PROJECTION,
            "schema_hash": hashlib.sha256(RELEASE_ID.encode()).digest(), "created_by": user.id,
        },
    )
    project.latest_published_release_id = RELEASE_ID
    db.commit()
    release = db.get(OntologyRelease, RELEASE_ID)

    db.add(SemanticSnapshot(
        id=SNAPSHOT_ID, ontology_release_id=release.id,
        quality_summary={"row_count": 1, "quality_score": 0.98},
        evidence_summary={"citations": [
            {
                "source_id": "source-supplier-api-001", "source_type": "csv",
                "locator": "fixture://source-supplier-api-001", "content_hash": "1" * 64,
            },
        ]},
        materialization_hash="a" * 64, status="materialized", created_by=user.id,
        # Task 20: this transport test exercises the ALLOW path end to end,
        # so the snapshot must carry real (fresh) freshness pins — a
        # snapshot with no governed refresh context defaults to "unknown"
        # and is denied outright by the Runtime freshness gate.
        freshness_state="fresh", freshness_lag_seconds=60,
        source_cursor={
            "source_id": "source-supplier-api-001", "resource": "default",
            "contract": "watermark_primary_key", "watermark": None, "primary_key": "1",
            "opaque_value": None, "observed_at": datetime.now(timezone.utc).isoformat(),
        },
    ))
    db.add(Action(id=ACTION_ID, ontology_id=ONTOLOGY_ID, name_cn="确认供应商状态", enabled=True))
    db.commit()

    entity = Entity(id="entity-supplier-api-001", ontology_id=ONTOLOGY_ID, name_cn="供应商", name_en="Supplier")
    db.add(entity)
    db.flush()
    db.add(EntityInstance(
        id="inst-api-sup001", entity_id=entity.id, ontology_id=ONTOLOGY_ID,
        row_identity="SUP001", row_data={"supplier_id": "SUP001", "status": "pending"}, revision=1,
    ))
    db.commit()


def _issue_token(db, *, scope: set[str]) -> str:
    return issue_delegated_credential(
        db, client_id=AGENT_ID, user_id=USER_ID, audience=AUDIENCE,
        scope=scope, ttl_seconds=300, now=datetime.now(timezone.utc),
    )


@pytest.fixture
def governed_state(db):
    _seed_governed_state(db)
    return db


@pytest.fixture
def runtime_headers(governed_state):
    token = _issue_token(governed_state, scope={"ontology:read", "ontology:write"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def missing_scope_headers(governed_state):
    # A credential issued without the "ontology:read" scope required by
    # POST /investigate — a credential-layer denial (SCOPE_DENIED), verified
    # before RuntimeService ever runs.
    token = _issue_token(governed_state, scope={"ontology:write"})
    return {"Authorization": f"Bearer {token}"}


def valid_investigation(**overrides):
    body = {
        "semantic_snapshot_id": SNAPSHOT_ID,
        "query": "supplier SUP001",
        "ontology_id": ONTOLOGY_ID,
        "entity_type": "Supplier",
        "filters": {},
        "limit": 20,
    }
    body.update(overrides)
    return body


def valid_action_plan_request(**overrides):
    body = {
        "semantic_snapshot_id": SNAPSHOT_ID,
        "action_id": ACTION_ID,
        "parameters": {"status": "confirmed"},
        "target_selector": None,
    }
    body.update(overrides)
    return body


def test_investigate_api_returns_normalized_result(client, runtime_headers):
    response = client.post(
        "/api/v2/runtime/investigate",
        json={
            "semantic_snapshot_id": "snap-valid-001",
            "query": "supplier SUP001",
            "ontology_id": "ontology-supply-001",
            "entity_type": "Supplier",
            "filters": {},
            "limit": 20,
        },
        headers=runtime_headers,
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "ALLOW"
    assert response.json()["semantic_snapshot_id"] == "snap-valid-001"


def test_authenticated_user_can_exchange_for_a_runtime_credential_and_investigate(client, governed_state):
    session_token = create_access_token({"sub": USER_ID, "role": "editor"})
    exchange = client.post(
        "/api/v2/runtime/delegations",
        json={"agent_id": AGENT_ID, "scopes": ["ontology:read"]},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert exchange.status_code == 200, exchange.text
    runtime_token = exchange.json()["token"]

    response = client.post(
        "/api/v2/runtime/investigate", json=valid_investigation(),
        headers={"Authorization": f"Bearer {runtime_token}"},
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "ALLOW"


def test_investigate_api_denial_does_not_return_protected_rows(client, missing_scope_headers):
    response = client.post("/api/v2/runtime/investigate", json=valid_investigation(), headers=missing_scope_headers)
    assert response.status_code == 403
    assert response.json()["decision"] == "DENY"
    assert response.json()["result"] is None


def test_investigate_api_returns_empty_allow_for_no_match(client, runtime_headers):
    response = client.post(
        "/api/v2/runtime/investigate",
        json=valid_investigation(query="no-such-supplier-anywhere"),
        headers=runtime_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "ALLOW"
    assert body["result"] == []


def test_investigate_api_rejects_body_identity_fields(client, runtime_headers):
    response = client.post(
        "/api/v2/runtime/investigate",
        json=valid_investigation(agent_id="agent-other", user_id="user-other"),
        headers=runtime_headers,
    )
    assert response.status_code == 422


def test_investigate_api_missing_credential_is_401(client):
    response = client.post("/api/v2/runtime/investigate", json=valid_investigation())
    assert response.status_code == 401
    assert response.json()["decision"] == "DENY"
    assert response.json()["reason_code"] == "MISSING_DELEGATION"


def test_investigate_api_release_only_request_is_denied(client, runtime_headers):
    response = client.post(
        "/api/v2/runtime/investigate",
        json=valid_investigation(semantic_snapshot_id=RELEASE_ID),
        headers=runtime_headers,
    )
    assert response.status_code == 403
    assert response.json()["reason_code"] == "SNAPSHOT_NOT_GOVERNED"
    assert response.json()["result"] is None


def test_create_action_plan_api_returns_immutable_plan_without_writing_production_data(client, runtime_headers):
    response = client.post(
        "/api/v2/runtime/action-plans",
        json=valid_action_plan_request(),
        headers=runtime_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["semantic_snapshot_id"] == SNAPSHOT_ID
    assert body["ontology_release_id"] == RELEASE_ID
    assert body["agent_id"] == AGENT_ID
    assert body["user_id"] == USER_ID
    assert body["parameters"] == {"status": "confirmed"}
    assert body["plan_hash"]


def test_create_action_plan_api_ignores_or_rejects_body_identity_fields(client, runtime_headers):
    response = client.post(
        "/api/v2/runtime/action-plans",
        json=valid_action_plan_request(agent_id="agent-other", user_id="user-other"),
        headers=runtime_headers,
    )
    assert response.status_code in (201, 422)
    if response.status_code == 201:
        body = response.json()
        assert body["agent_id"] == AGENT_ID
        assert body["user_id"] == USER_ID


def test_create_action_plan_api_rejects_ineligible_action(client, runtime_headers):
    response = client.post(
        "/api/v2/runtime/action-plans",
        json=valid_action_plan_request(action_id="no-such-action"),
        headers=runtime_headers,
    )
    assert response.status_code == 403
    assert response.json()["decision"] == "DENY"
    assert response.json()["reason_code"] == "ACTION_NOT_ELIGIBLE"


def test_get_action_plan_api_returns_the_created_plan(client, runtime_headers):
    create_response = client.post(
        "/api/v2/runtime/action-plans", json=valid_action_plan_request(), headers=runtime_headers,
    )
    plan_id = create_response.json()["id"]

    response = client.get(f"/api/v2/runtime/action-plans/{plan_id}", headers=runtime_headers)
    assert response.status_code == 200
    assert response.json()["id"] == plan_id
    assert response.json()["plan_hash"] == create_response.json()["plan_hash"]


def test_get_action_plan_api_canonical_plan_hash_ignores_generated_storage_id(client, runtime_headers):
    """Task 19: `compute_plan_hash` (`app.services.runtime.canonical`)
    excludes the generated storage id (`id`) from the canonical plan
    fields — fetching the same immutable plan back through REST twice must
    still hash the same even though nothing about the *semantic* proposal
    changed."""
    create_response = client.post(
        "/api/v2/runtime/action-plans", json=valid_action_plan_request(), headers=runtime_headers,
    )
    plan_id = create_response.json()["id"]

    fetched = client.get(f"/api/v2/runtime/action-plans/{plan_id}", headers=runtime_headers).json()
    created = create_response.json()
    assert compute_plan_hash(created) == compute_plan_hash(fetched)

    relabeled = dict(fetched)
    relabeled["id"] = "some-other-generated-id"
    assert compute_plan_hash(relabeled) == compute_plan_hash(fetched)


def test_investigate_api_normalized_decision_excludes_correlation_id(client, runtime_headers):
    """Task 19: `normalize_investigation` never carries `correlation_id` (a
    tracing id) — two REST calls that only differ by their transport-issued
    correlation id must normalize identically."""
    response = client.post(
        "/api/v2/runtime/investigate", json=valid_investigation(), headers=runtime_headers,
    )
    body = response.json()
    assert "correlation_id" not in normalize_investigation(body)

    relabeled = dict(body)
    relabeled["correlation_id"] = "some-other-correlation-id"
    assert normalize_investigation(relabeled) == normalize_investigation(body)


def test_get_action_plan_api_denies_unknown_plan(client, runtime_headers):
    response = client.get("/api/v2/runtime/action-plans/no-such-plan", headers=runtime_headers)
    assert response.status_code == 403
    assert response.json()["decision"] == "DENY"


def test_execution_status_api_returns_stable_not_started_status(client, runtime_headers):
    create_response = client.post(
        "/api/v2/runtime/action-plans", json=valid_action_plan_request(), headers=runtime_headers,
    )
    plan_id = create_response.json()["id"]

    response = client.get(f"/api/v2/runtime/execution-status/{plan_id}", headers=runtime_headers)
    assert response.status_code == 200
    assert response.json()["plan_id"] == plan_id
    assert response.json()["status"] == "not_started"


def test_execution_status_api_denies_unknown_plan(client, runtime_headers):
    response = client.get("/api/v2/runtime/execution-status/no-such-plan", headers=runtime_headers)
    assert response.status_code == 403
    assert response.json()["decision"] == "DENY"
