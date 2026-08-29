"""Task 19: normalized results and plan hashes are transport-independent.

Every Runtime transport — REST (Task 16), the Python SDK (Task 17), MCP, and
the built-in reference Agent (both Task 18) — calls the exact same
`RuntimeService` (Task 15). This module proves the Milestone 2/3 Scope
Amendment's *tiered* parity holds:

- All four transports must agree on the normalized *decision*
  (`normalize_investigation`'s decision/reason_code/snapshot pin/evidence).
- Only REST and the SDK are held to a byte-identical canonical `plan_hash`
  (`compute_plan_hash`) — MCP and the reference Agent are compatibility
  adapters, not the product's defining layer, and are not asserted here.

REST and the SDK are exercised for real: REST through the app's own
`TestClient`, and the SDK's `RuntimeClient` wired to that same `TestClient`
as its `HttpTransport` — so the SDK path really serializes a request,
crosses the real REST router, and decodes the real response, never calling
`RuntimeService` directly. MCP and the reference Agent are exercised through
their own dispatch entry points (`call_runtime_tool`, `ReferenceAgentRuntime`)
exactly like `tests/runtime/test_mcp_runtime_adapter.py` does.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

import pytest
from sqlalchemy import text

import app.services.runtime.service as service_module
from app.models.action import Action
from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.oauth import OAuthClient
from app.models.ontology import OntologyProject
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.ontology_release import OntologyRelease
from app.models.semantic_snapshot import SemanticSnapshot
from app.models.user import User
from app.schemas.runtime import InvestigationRequest
from app.services.mcp_tools import call_runtime_tool
from app.services.runtime.canonical import (
    canonical_plan_fields,
    compute_plan_hash,
    normalize_investigation,
)
from app.services.runtime.credentials import (
    RuntimeContext,
    RuntimePrincipal,
    issue_delegated_credential,
)
from app.services.runtime.reference_agent import ReferenceAgentRuntime
from app.services.runtime.service import ActionPlanRequest, RuntimeService

from ontexus_runtime.client import RuntimeClient
from ontexus_runtime.errors import RuntimeDeniedError
from ontexus_runtime.models import ActionPlanRequest as SdkActionPlanRequest
from ontexus_runtime.models import InvestigationRequest as SdkInvestigationRequest

AUDIENCE = "ontexus-runtime"
DOMAIN = "00000000-0000-0000-0000-0000000000ee"
ONTOLOGY_ID = "ontology-parity-001"
RELEASE_ID = "release-parity-001"
SNAPSHOT_ID = "snap-parity-001"
USER_ID = "user-parity-001"
ACTION_ID = "action-parity-001"
AGENT_ALLOW_ID = "agent-parity-allow-001"
AGENT_DENY_ID = "agent-parity-deny-001"
INSTANCE_ID = "inst-parity-001"
ENTITY_ID = "entity-parity-supplier-001"
MANIFEST_PROJECTION = '{"entities":[{"id":"entity-parity-supplier-001"}]}'
FULL_CAPABILITIES = ["investigate", "propose_action"]
DENY_CAPABILITIES = ["propose_action"]

INVESTIGATION_FIXTURES: dict[str, dict[str, Any]] = {
    "parity-allow-001": {"agent_id": AGENT_ALLOW_ID, "query": "SUP001", "entity_type": "Supplier"},
    "parity-deny-001": {"agent_id": AGENT_DENY_ID, "query": "SUP001", "entity_type": "Supplier"},
    "parity-empty-001": {"agent_id": AGENT_ALLOW_ID, "query": "no-such-supplier-anywhere", "entity_type": "Supplier"},
}


def _seed_parity_state(db) -> None:
    """One fully governed snapshot reachable through every Runtime
    transport, plus a second, deliberately under-capable Agent identity
    (`AGENT_DENY_ID`) so a DENY case is reachable the same way through every
    transport too — without mutating a shared Agent's capabilities between
    fixtures (mirrors `tests/runtime/test_runtime_api.py`'s seed shape)."""
    user = User(
        id=USER_ID, username=USER_ID, email=f"{USER_ID}@example.invalid",
        password_hash="not-a-real-password-hash", role="editor", security_domain_id=DOMAIN,
    )
    db.add(user)
    db.commit()

    db.add(OAuthClient(
        id=AGENT_ALLOW_ID, client_name="Parity Agent (allowed)", redirect_uris=[],
        allowed_scopes=["ontology:read", "ontology:write"], is_active=True, created_by=USER_ID,
        security_domain_id=DOMAIN, allowed_audiences=[AUDIENCE], capability_names=list(FULL_CAPABILITIES),
    ))
    db.add(OAuthClient(
        id=AGENT_DENY_ID, client_name="Parity Agent (missing investigate)", redirect_uris=[],
        allowed_scopes=["ontology:read", "ontology:write"], is_active=True, created_by=USER_ID,
        security_domain_id=DOMAIN, allowed_audiences=[AUDIENCE], capability_names=list(DENY_CAPABILITIES),
    ))
    db.add(OntologyDataGrant(
        id=str(uuid.uuid4()), ontology_id=ONTOLOGY_ID, user_id=USER_ID,
        capabilities=list(FULL_CAPABILITIES), status="active", created_by=USER_ID,
    ))
    db.commit()

    project = OntologyProject(
        id=ONTOLOGY_ID, name="parity ontology", domain="test",
        created_by=user.id, security_domain_id=user.security_domain_id,
    )
    db.add(project)
    db.flush()
    # `manifest_projection` uses Task 11's PostgreSQL-only CanonicalJSONB
    # type; seed with a plain SQL insert, exactly like
    # tests/runtime/test_runtime_service.py's `_seed_release` does.
    db.execute(
        text(
            "INSERT INTO ontology_releases "
            "(id, ontology_id, version_no, version, manifest_bytes, "
            "manifest_projection, schema_hash, status, created_by, created_at) "
            "VALUES (:id, :ontology_id, 1, 'v1', :manifest, "
            ":manifest_projection, :schema_hash, 'published', :created_by, CURRENT_TIMESTAMP)"
        ),
        {
            "id": RELEASE_ID, "ontology_id": ONTOLOGY_ID, "manifest": b"snapshot-manifest",
            "manifest_projection": MANIFEST_PROJECTION,
            "schema_hash": hashlib.sha256(RELEASE_ID.encode()).digest(),
            "created_by": user.id,
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
                "source_id": "source-parity-001", "source_type": "csv",
                "locator": "fixture://source-parity-001", "content_hash": "1" * 64,
            },
        ]},
        materialization_hash="a" * 64, status="materialized", created_by=user.id,
    ))
    db.add(Action(id=ACTION_ID, ontology_id=ONTOLOGY_ID, name_cn="确认供应商状态", enabled=True))
    db.commit()

    entity = Entity(id=ENTITY_ID, ontology_id=ONTOLOGY_ID, name_cn="供应商", name_en="Supplier")
    db.add(entity)
    db.flush()
    db.add(EntityInstance(
        id=INSTANCE_ID, entity_id=entity.id, ontology_id=ONTOLOGY_ID,
        row_identity="SUP001", row_data={"supplier_id": "SUP001", "status": "pending"}, revision=1,
    ))
    db.commit()


@pytest.fixture
def parity_env(client, db):
    _seed_parity_state(db)
    return {"client": client, "db": db}


class _StaticCredential:
    """The simplest SDK `CredentialProvider` — always returns the same
    already-issued delegated credential (mirrors `sdk/tests/test_runtime_client.py`'s
    `StaticCredential`)."""

    def __init__(self, token: str) -> None:
        self._token = token

    def get_delegation(self) -> str:
        return self._token


class _ClientTransport:
    """The SDK's `HttpTransport` wired to the real FastAPI app under test,
    through the same `TestClient` the REST assertions use — so the SDK path
    crosses the real router and JSON encode/decode boundary, not a stub."""

    def __init__(self, client) -> None:
        self._client = client

    def request(
        self, method: str, path: str, json: Mapping[str, object] | None, headers: Mapping[str, str],
    ) -> Mapping[str, object]:
        response = self._client.request(method, path, json=json, headers=dict(headers))
        return response.json()


def _issue_runtime_token(env, agent_id: str) -> str:
    return issue_delegated_credential(
        env["db"], client_id=agent_id, user_id=USER_ID, audience=AUDIENCE,
        scope={"ontology:read", "ontology:write"}, ttl_seconds=300, now=datetime.now(timezone.utc),
    )


def _runtime_context(agent_id: str) -> RuntimeContext:
    principal = RuntimePrincipal(
        agent_id=agent_id, user_id=USER_ID, security_domain_id=DOMAIN,
        audience="ontexus-reference", scope=frozenset({"ontology:read", "ontology:write"}),
        token_id=f"tok-{agent_id}",
    )
    return RuntimeContext(principal=principal, correlation_id=f"corr-{agent_id}")


def _investigate_payload(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "semantic_snapshot_id": SNAPSHOT_ID,
        "ontology_id": ONTOLOGY_ID,
        "query": fixture["query"],
        "entity_type": fixture["entity_type"],
        "filters": {},
        "limit": 20,
    }


def _sdk_client(env, agent_id: str) -> RuntimeClient:
    token = _issue_runtime_token(env, agent_id)
    return RuntimeClient("https://runtime.test", _StaticCredential(token), _ClientTransport(env["client"]))


def invoke_fixture_transport(env, transport: str, operation: str, fixture_id: str) -> Any:
    if operation == "investigate":
        fixture = INVESTIGATION_FIXTURES[fixture_id]
        payload = _investigate_payload(fixture)
        if transport == "rest":
            headers = {"Authorization": f"Bearer {_issue_runtime_token(env, fixture['agent_id'])}"}
            response = env["client"].post("/api/v2/runtime/investigate", json=payload, headers=headers)
            return response.json()
        if transport == "sdk":
            try:
                return _sdk_client(env, fixture["agent_id"]).investigate(SdkInvestigationRequest(**payload))
            except RuntimeDeniedError as exc:
                # The SDK raises rather than returning a body for a denial —
                # `normalize_investigation` must read the same decision
                # content straight off the exception (see its docstring).
                return exc
        if transport == "mcp":
            context = _runtime_context(fixture["agent_id"])
            return call_runtime_tool(env["db"], context, "runtime_investigate", payload)
        if transport == "reference-agent":
            context = _runtime_context(fixture["agent_id"])
            request = InvestigationRequest(**payload)
            return ReferenceAgentRuntime().investigate(request, context, env["db"])
        raise ValueError(f"unknown transport: {transport}")

    if operation == "create_action_plan":
        payload = {
            "semantic_snapshot_id": SNAPSHOT_ID,
            "action_id": ACTION_ID,
            "parameters": {"status": "confirmed"},
            "target_selector": None,
            "idempotency_key": f"{fixture_id}-idem",
        }
        if transport == "rest":
            headers = {"Authorization": f"Bearer {_issue_runtime_token(env, AGENT_ALLOW_ID)}"}
            response = env["client"].post("/api/v2/runtime/action-plans", json=payload, headers=headers)
            return response.json()
        if transport == "sdk":
            return _sdk_client(env, AGENT_ALLOW_ID).create_action_plan(SdkActionPlanRequest(**payload))
        raise ValueError(f"unknown transport: {transport}")

    raise ValueError(f"unknown operation: {operation}")


def expected_normalized(env, fixture_id: str) -> dict[str, Any]:
    """The ground-truth normalized decision for `fixture_id`, computed by
    calling `RuntimeService` directly — independent of any of the four
    transports under test."""
    fixture = INVESTIGATION_FIXTURES[fixture_id]
    request = InvestigationRequest(**_investigate_payload(fixture))
    context = _runtime_context(fixture["agent_id"])
    result = RuntimeService().investigate(request, context, env["db"])
    return normalize_investigation(result)


def change_frozen_target(plan: Any, target_value: str) -> Any:
    """Mutate a copy of `plan`'s normalized target — proving
    `canonical_plan_fields` actually depends on it, rather than silently
    reproducing whatever `plan_hash` the server happened to compute."""
    if isinstance(plan, Mapping):
        mutated = dict(plan)
        mutated["target_key"] = [target_value]
        return mutated
    return plan.model_copy(update={"target_key": [target_value]})


class _FrozenDateTime(datetime):
    """A fixed clock for `app.services.runtime.service`'s `datetime.now`,
    so two independent `create_action_plan` calls (one REST, one SDK) made
    moments apart still compute the same `expiry` — the only reason their
    canonical plan fields would otherwise differ despite describing the
    exact same proposal."""

    _FIXED = datetime(2026, 1, 1, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._FIXED.astimezone(tz) if tz else cls._FIXED.replace(tzinfo=None)


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(service_module, "datetime", _FrozenDateTime)


# ------------------------------------------------------- investigation parity

@pytest.mark.parametrize("fixture_id", ["parity-allow-001", "parity-deny-001", "parity-empty-001"])
@pytest.mark.parametrize("transport", ["rest", "sdk", "mcp", "reference-agent"])
def test_equivalent_investigation_has_the_same_normalized_decision(parity_env, transport, fixture_id):
    result = invoke_fixture_transport(parity_env, transport, "investigate", fixture_id)
    normalized = normalize_investigation(result)
    expected = expected_normalized(parity_env, fixture_id)
    assert normalized["decision"] == expected["decision"]
    assert normalized["reason_code"] == expected["reason_code"]
    assert normalized["semantic_snapshot_id"] == expected["semantic_snapshot_id"]
    assert normalized["evidence_citations"] == expected["evidence_citations"]


@pytest.mark.parametrize("fixture_id", ["parity-allow-001", "parity-empty-001"])
@pytest.mark.parametrize("transport", ["rest", "sdk"])
def test_rest_and_sdk_are_byte_identical(parity_env, transport, fixture_id):
    result = invoke_fixture_transport(parity_env, transport, "investigate", fixture_id)
    assert normalize_investigation(result) == expected_normalized(parity_env, fixture_id)


def test_rest_and_sdk_plan_hash_matches_and_changes_on_semantic_drift(parity_env, frozen_clock):
    rest_plan = invoke_fixture_transport(parity_env, "rest", "create_action_plan", "parity-plan-001")
    sdk_plan = invoke_fixture_transport(parity_env, "sdk", "create_action_plan", "parity-plan-001")
    assert compute_plan_hash(rest_plan) == compute_plan_hash(sdk_plan)
    assert compute_plan_hash(change_frozen_target(rest_plan, "target-002")) != compute_plan_hash(rest_plan)


def test_canonical_plan_fields_excludes_generated_storage_id(parity_env, frozen_clock):
    """`id` is a generated storage id (Task 19's canonicalizer explicitly
    excludes it) — two plans that differ only in `id` must hash the same."""
    rest_plan = invoke_fixture_transport(parity_env, "rest", "create_action_plan", "parity-plan-002")
    other_id_plan = dict(rest_plan)
    other_id_plan["id"] = "some-other-generated-id"
    assert compute_plan_hash(rest_plan) == compute_plan_hash(other_id_plan)
