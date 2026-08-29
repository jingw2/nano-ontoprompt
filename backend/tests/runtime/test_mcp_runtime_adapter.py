"""Task 18: MCP and the built-in reference Agent are reference adapters onto
the exact same `RuntimeService` (Task 15) that REST (Task 16) and the SDK
(Task 17) use.

`call_runtime_tool` (`app.services.mcp_tools`) is MCP's dispatch entry point
for the four `runtime_*` tool names; `ReferenceAgentRuntime`
(`app.services.runtime.reference_agent`) and `LangGraphRuntimeAdapter`
(`app.runtime.langgraph_adapter`) are the built-in Agent's equivalent.
Neither re-implements policy or query logic — both build a request model
from caller-supplied arguments only (never a caller-asserted identity) and
call `RuntimeService` directly.
"""
from __future__ import annotations

import hashlib
import uuid

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
from app.runtime.langgraph_adapter import LangGraphRuntimeAdapter
from app.schemas.runtime import InvestigationRequest
from app.services.mcp_tools import McpToolError, call_runtime_tool
from app.services.runtime.canonical import normalize_investigation
from app.services.runtime.credentials import RuntimeContext, RuntimePrincipal
from app.services.runtime.reference_agent import ReferenceAgentRuntime
from app.services.runtime.service import ActionPlanRequest, RuntimeService

DOMAIN = "00000000-0000-0000-0000-0000000000dd"
CAPABILITIES = ["investigate", "propose_action"]
ONTOLOGY_ID = "ontology-mcp-001"
RELEASE_ID = "release-valid-001"
SNAPSHOT_ID = "snap-valid-001"
AGENT_ID = "agent-mcp-001"
USER_ID = "user-mcp-001"
ACTION_ID = "action-mcp-001"
MANIFEST_PROJECTION = '{"entities":[{"id":"entity-mcp-supplier-001"}]}'


def _seed_governed_state(db):
    """A fully governed snapshot reachable through the MCP/reference-agent
    adapters: a published release, an active Agent (an `OAuthClient`
    doubling as a Task 13 registered identity) with both Runtime
    capabilities, an active data grant for the delegated user, and one
    eligible Action."""
    user = User(
        id=USER_ID, username=USER_ID, email=f"{USER_ID}@example.invalid",
        password_hash="not-a-real-password-hash", role="editor", security_domain_id=DOMAIN,
    )
    db.add(user)
    db.commit()

    client = OAuthClient(
        id=AGENT_ID, client_name="Runtime MCP Agent", redirect_uris=[], allowed_scopes=[],
        is_active=True, created_by=USER_ID, security_domain_id=DOMAIN,
        allowed_audiences=[], capability_names=list(CAPABILITIES),
    )
    db.add(client)
    db.add(OntologyDataGrant(
        id=str(uuid.uuid4()), ontology_id=ONTOLOGY_ID, user_id=USER_ID,
        capabilities=list(CAPABILITIES), status="active", created_by=USER_ID,
    ))
    db.commit()

    project = OntologyProject(
        id=ONTOLOGY_ID, name="mcp ontology", domain="test",
        created_by=USER_ID, security_domain_id=DOMAIN,
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
            "created_by": USER_ID,
        },
    )
    project.latest_published_release_id = RELEASE_ID
    db.commit()
    release = db.get(OntologyRelease, RELEASE_ID)

    snapshot = SemanticSnapshot(
        id=SNAPSHOT_ID, ontology_release_id=release.id,
        quality_summary={"row_count": 1, "quality_score": 0.99},
        evidence_summary={"citations": [
            {"source_id": "source-mcp-001", "source_type": "csv",
             "locator": "fixture://source-mcp-001", "content_hash": "1" * 64},
        ]},
        materialization_hash="a" * 64, status="materialized", created_by=USER_ID,
    )
    db.add(snapshot)
    db.add(Action(id=ACTION_ID, ontology_id=ONTOLOGY_ID, name_cn="确认状态", enabled=True))
    db.commit()
    return release, snapshot


def _seed_entity_instance(db, *, instance_id="inst-mcp-001"):
    entity = Entity(id="entity-mcp-supplier-001", ontology_id=ONTOLOGY_ID, name_cn="供应商", name_en="Supplier")
    db.add(entity)
    db.flush()
    instance = EntityInstance(
        id=instance_id, entity_id=entity.id, ontology_id=ONTOLOGY_ID,
        row_identity="SUP001", row_data={"supplier_id": "SUP001", "status": "pending"}, revision=1,
    )
    db.add(instance)
    db.commit()
    return instance


@pytest.fixture
def governed_snapshot(db):
    return _seed_governed_state(db)[1]


@pytest.fixture
def runtime_context():
    # A properly-authorized caller for these tests carries both scopes — the
    # scope-*enforcement* itself (a read-only-scoped principal must be denied
    # `runtime_create_action_plan`) is exercised separately below by
    # `test_mcp_runtime_create_action_plan_denies_read_only_scope`, which
    # builds its own deliberately read-only-scoped `RuntimeContext`.
    principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id=USER_ID, security_domain_id=DOMAIN,
        audience="ontexus-mcp", scope=frozenset({"ontology:read", "ontology:write"}), token_id="tok-mcp-001",
    )
    return RuntimeContext(principal=principal, correlation_id="corr-mcp-001")


def valid_investigation_dict(**overrides):
    defaults = dict(
        semantic_snapshot_id=SNAPSHOT_ID, ontology_id=ONTOLOGY_ID,
        query=None, entity_type=None, filters={}, limit=20,
    )
    defaults.update(overrides)
    return defaults


# --------------------------------------------------------------- MCP tools

def test_mcp_runtime_investigate_matches_service(db, governed_snapshot, runtime_context):
    result = call_runtime_tool(db, runtime_context, "runtime_investigate", valid_investigation_dict())
    assert result["decision"] == "ALLOW"
    assert result["semantic_snapshot_id"] == "snap-valid-001"


def test_mcp_runtime_investigate_returns_snapshot_scoped_result(db, governed_snapshot, runtime_context):
    _seed_entity_instance(db)
    result = call_runtime_tool(
        db, runtime_context, "runtime_investigate",
        valid_investigation_dict(query="SUP001", entity_type="Supplier"),
    )
    assert result["decision"] == "ALLOW"
    assert result["result"] == [{
        "instance_id": "inst-mcp-001", "entity_id": "entity-mcp-supplier-001",
        "revision": 1, "row_data": {"supplier_id": "SUP001", "status": "pending"},
    }]


def test_mcp_identity_arguments_cannot_impersonate_principal(db, governed_snapshot, runtime_context):
    result = call_runtime_tool(
        db, runtime_context, "runtime_investigate",
        {**valid_investigation_dict(), "agent_id": "agent-other", "user_id": "user-other"},
    )
    assert result["agent_id"] != "agent-other" or "agent_id" not in result


def test_legacy_release_only_mcp_request_is_denied(db, runtime_context):
    with pytest.raises(McpToolError) as exc:
        call_runtime_tool(db, runtime_context, "runtime_investigate", {"release_id": "release-valid-001"})
    assert exc.value.code == "SNAPSHOT_NOT_GOVERNED"


def test_mcp_runtime_investigate_denies_when_agent_lacks_capability(db, governed_snapshot):
    principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id=USER_ID, security_domain_id=DOMAIN,
        audience="ontexus-mcp", scope=frozenset({"ontology:read"}), token_id="tok-mcp-002",
    )
    context = RuntimeContext(principal=principal, correlation_id="corr-mcp-002")
    db.query(OAuthClient).filter(OAuthClient.id == AGENT_ID).one().capability_names = ["propose_action"]
    db.commit()

    result = call_runtime_tool(db, context, "runtime_investigate", valid_investigation_dict())
    assert result["decision"] == "DENY"
    assert result["reason_code"] == "AGENT_CAPABILITY_DENIED"
    assert result["result"] is None


def test_mcp_runtime_create_and_get_action_plan(db, governed_snapshot, runtime_context):
    created = call_runtime_tool(
        db, runtime_context, "runtime_create_action_plan",
        {"semantic_snapshot_id": SNAPSHOT_ID, "action_id": ACTION_ID, "parameters": {"status": "confirmed"}},
    )
    assert created["agent_id"] == AGENT_ID
    assert created["user_id"] == USER_ID

    fetched = call_runtime_tool(db, runtime_context, "runtime_get_action_plan", {"plan_id": created["id"]})
    assert fetched["plan_hash"] == created["plan_hash"]

    status = call_runtime_tool(db, runtime_context, "runtime_get_execution_status", {"plan_id": created["id"]})
    assert status == {
        "plan_id": created["id"], "status": "not_started", "correlation_id": runtime_context.correlation_id,
    }


def test_mcp_create_action_plan_ignores_caller_identity_arguments(db, governed_snapshot, runtime_context):
    created = call_runtime_tool(
        db, runtime_context, "runtime_create_action_plan",
        {"semantic_snapshot_id": SNAPSHOT_ID, "action_id": ACTION_ID, "parameters": {},
         "agent_id": "agent-other", "user_id": "user-other"},
    )
    assert created["agent_id"] == AGENT_ID
    assert created["user_id"] == USER_ID


def test_mcp_get_action_plan_denies_other_principal(db, governed_snapshot, runtime_context):
    created = call_runtime_tool(
        db, runtime_context, "runtime_create_action_plan",
        {"semantic_snapshot_id": SNAPSHOT_ID, "action_id": ACTION_ID, "parameters": {}},
    )
    other_principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id="user-other-001", security_domain_id=DOMAIN,
        audience="ontexus-mcp", scope=frozenset({"ontology:read"}), token_id="tok-mcp-003",
    )
    other_context = RuntimeContext(principal=other_principal, correlation_id="corr-mcp-003")
    with pytest.raises(McpToolError) as exc:
        call_runtime_tool(db, other_context, "runtime_get_action_plan", {"plan_id": created["id"]})
    assert exc.value.code == "POLICY_DENIED"


def test_mcp_runtime_create_action_plan_denies_read_only_scope(db, governed_snapshot):
    """The critical-finding regression test: an MCP session whose access
    token was granted only `ontology:read` must not be able to call
    `runtime_create_action_plan` (a write operation) — REST's equivalent
    path (`_require_runtime_context(WRITE_SCOPE)` in
    `app.routers.v2.runtime`) rejects the identical request with
    `SCOPE_DENIED` before ever reaching `RuntimeService`, and MCP must
    match that exactly. Uses a real, fully-governed snapshot/action/grant
    (the same fixture a genuinely authorized call in this file would
    succeed against) so the denial is proven to come from the scope check
    itself, not from missing setup."""
    principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id=USER_ID, security_domain_id=DOMAIN,
        audience="ontexus-mcp", scope=frozenset({"ontology:read"}), token_id="tok-mcp-readonly",
    )
    read_only_context = RuntimeContext(principal=principal, correlation_id="corr-mcp-readonly")

    with pytest.raises(McpToolError) as exc:
        call_runtime_tool(
            db, read_only_context, "runtime_create_action_plan",
            {"semantic_snapshot_id": SNAPSHOT_ID, "action_id": ACTION_ID, "parameters": {"status": "confirmed"}},
        )
    assert exc.value.code == "SCOPE_DENIED"

    # No `RuntimePlan` was ever persisted for the denied call.
    from app.models.runtime_plan import RuntimePlan
    assert db.query(RuntimePlan).filter(RuntimePlan.agent_id == AGENT_ID).count() == 0


def test_mcp_runtime_investigate_denies_write_only_scope(db, governed_snapshot):
    """Symmetric check: a write-only-scoped session cannot call
    `runtime_investigate` (a read operation), matching REST's
    `_require_runtime_context(READ_SCOPE)` for `POST /investigate`."""
    principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id=USER_ID, security_domain_id=DOMAIN,
        audience="ontexus-mcp", scope=frozenset({"ontology:write"}), token_id="tok-mcp-writeonly",
    )
    write_only_context = RuntimeContext(principal=principal, correlation_id="corr-mcp-writeonly")

    with pytest.raises(McpToolError) as exc:
        call_runtime_tool(db, write_only_context, "runtime_investigate", valid_investigation_dict())
    assert exc.value.code == "SCOPE_DENIED"


def test_mcp_runtime_get_action_plan_denies_read_scope_missing(db, governed_snapshot, runtime_context):
    created = call_runtime_tool(
        db, runtime_context, "runtime_create_action_plan",
        {"semantic_snapshot_id": SNAPSHOT_ID, "action_id": ACTION_ID, "parameters": {}},
    )
    principal = RuntimePrincipal(
        agent_id=AGENT_ID, user_id=USER_ID, security_domain_id=DOMAIN,
        audience="ontexus-mcp", scope=frozenset({"ontology:write"}), token_id="tok-mcp-writeonly-2",
    )
    write_only_context = RuntimeContext(principal=principal, correlation_id="corr-mcp-writeonly-2")

    with pytest.raises(McpToolError) as exc:
        call_runtime_tool(db, write_only_context, "runtime_get_action_plan", {"plan_id": created["id"]})
    assert exc.value.code == "SCOPE_DENIED"


def test_mcp_runtime_tool_unknown_name_rejected(db, runtime_context):
    with pytest.raises(McpToolError) as exc:
        call_runtime_tool(db, runtime_context, "runtime_no_such_tool", {})
    assert exc.value.code == "TOOL_UNKNOWN"


# --------------------------------------------------------- reference Agent

def test_reference_agent_investigate_matches_runtime_service(db, governed_snapshot, runtime_context):
    request = InvestigationRequest(**valid_investigation_dict())
    expected = RuntimeService().investigate(request, runtime_context, db)
    actual = ReferenceAgentRuntime().investigate(request, runtime_context, db)
    assert actual == expected


def test_mcp_and_reference_agent_agree_on_normalized_decision(db, governed_snapshot, runtime_context):
    """Task 19's tiered parity: MCP and the reference Agent are not held to
    a byte-identical `plan_hash`/wire shape (MCP returns a plain dict,
    `ReferenceAgentRuntime` returns the backend's own typed
    `InvestigationResult`), but `normalize_investigation` must still reduce
    both to the exact same normalized decision."""
    mcp_result = call_runtime_tool(db, runtime_context, "runtime_investigate", valid_investigation_dict())
    reference_result = ReferenceAgentRuntime().investigate(
        InvestigationRequest(**valid_investigation_dict()), runtime_context, db,
    )
    assert normalize_investigation(mcp_result) == normalize_investigation(reference_result)


def test_reference_agent_create_action_plan_matches_runtime_service(db, governed_snapshot, runtime_context):
    plan = ReferenceAgentRuntime().create_action_plan(
        ActionPlanRequest(semantic_snapshot_id=SNAPSHOT_ID, action_id=ACTION_ID, parameters={"status": "confirmed"}),
        runtime_context, db,
    )
    assert plan.agent_id == AGENT_ID
    assert plan.user_id == USER_ID
    assert plan.semantic_snapshot_id == SNAPSHOT_ID


def test_langgraph_adapter_investigate_routes_through_runtime_service(db, governed_snapshot, runtime_context):
    request = InvestigationRequest(**valid_investigation_dict())
    expected = RuntimeService().investigate(request, runtime_context, db)
    actual = LangGraphRuntimeAdapter().investigate(request, runtime_context, db)
    assert actual == expected


def test_langgraph_adapter_create_action_plan_routes_through_runtime_service(db, governed_snapshot, runtime_context):
    plan = LangGraphRuntimeAdapter().create_action_plan(
        ActionPlanRequest(semantic_snapshot_id=SNAPSHOT_ID, action_id=ACTION_ID, parameters={"status": "confirmed"}),
        runtime_context, db,
    )
    assert plan.agent_id == AGENT_ID
    assert plan.semantic_snapshot_id == SNAPSHOT_ID
