"""Task 3: `DeepSeekVisionCaller` wired into the real Agent turn path.

A turn whose context carries `extra["business_journey"]` is driven through
the SAME production `DeepSeekVisionCaller` (`app.services.model_callers.
deepseek_vision`) the ontology completion uses — never a provider-agnostic
`llm_service.chat_completion` path, never a second ad-hoc client. Only the
DeepSeek transport is faked (`DeepSeekVisionClient._client`, the same
injection point `tests/agent/test_business_journey_model_caller.py` and
`evals/business_journeys/test_orchestrator.py` already use); everything
else — `LangGraphRuntime`, `ToolGateway`-shaped tool dispatch, event
persistence — runs for real against the SQLite unit harness.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject
from app.models.user import User
import app.services.model_config_selector as model_config_selector
from app.runtime.langgraph_runtime import LangGraphRuntime, RuntimeModelError
from app.runtime.protocol import TurnRuntimeContext
from app.services.runtime.turn_plans import create_governed_plan_from_turn
from evals.business_journeys.contracts import (
    MODEL_ID,
    OFFICIAL_ORIGIN,
    ImmutableModelConfigVersion,
    ModelCallLedger,
    ModelConfigurationError,
)
from evals.business_journeys.deepseek_client import DeepSeekVisionClient

RUN_ID = "journey-model-contract"
JOURNEY_ID = "supply_chain"
# The real, checked-in fixture's own `low_risk_action` for JOURNEY_ID
# (test_data/runtime/supply_chain/semantic_minima.json) — `_prepare` now
# resolves this SAME value for every business-journey turn (second review
# round), so any test that supplies `automatic_action` must use this exact
# string or the schema/server-side validation rejects it.
JOURNEY_LOW_RISK_ACTION = "risk_label"


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(DeepSeekVisionClient, "_sleep", staticmethod(lambda seconds: None))


@pytest.fixture
def deepseek_transport(monkeypatch):
    """Installs a fake DeepSeek transport and returns the mutable queue of
    structured JSON bodies it will hand back, in call order. `models_
    available` (default: the real MODEL_ID) lets a test simulate a failed
    `/models` preflight probe without touching anything else."""
    queue: list[dict] = []
    calls: list[str] = []
    state = SimpleNamespace(queue=queue, calls=calls, models_available=[MODEL_ID], preflight_calls=0)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            state.preflight_calls += 1
            return httpx.Response(200, json={"data": [{"id": m} for m in state.models_available]})
        calls.append(request.url.path)
        structured = queue.pop(0)
        return httpx.Response(200, json={
            "model": MODEL_ID,
            "choices": [{"message": {"content": json.dumps(structured)}}],
        })

    transport = httpx.MockTransport(handle)
    original_client = DeepSeekVisionClient._client

    def patched_client(self):
        client = original_client(self)
        client.close()
        return httpx.Client(timeout=self._timeout_seconds, follow_redirects=False, transport=transport,
                            headers={"Authorization": f"Bearer {self._api_key}"})

    monkeypatch.setattr(DeepSeekVisionClient, "_client", patched_client)
    return state


@pytest.fixture
def deepseek_api_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")


@pytest.fixture
def pinned_model_config_version_id(monkeypatch):
    """`select_journey_model_config`'s active-pin check reads `model_configs.
    active_version_id` — a column the real, versioned `0004` migration adds
    in production but that the plain `ModelConfig` ORM class (and therefore
    the SQLite unit harness, which builds tables from ONLY that class's
    declared columns) does not have at all; `versioning_schema_present`
    itself documents this exact "pre-0004 unit harness" gap. Task 2's own
    `tests/agent/test_business_journey_model_caller.py` already works around
    it with a fake DB double rather than a real row for the same function;
    this mirrors that precedent by monkeypatching the resolver's return
    value directly, which still exercises everything downstream for real
    (the caller construction, the two completions, the ledger, the events)."""
    version_id = str(uuid.uuid4())
    immutable = ImmutableModelConfigVersion(
        version_id=version_id, provider="deepseek", model_id=MODEL_ID,
        origin=OFFICIAL_ORIGIN, behavior_hash="behavior-hash", frozen_at=None,
    )
    monkeypatch.setattr(model_config_selector, "select_journey_model_config",
                        lambda vid, db=None: immutable)
    return version_id


@pytest.fixture
def mismatched_model_config_version_id(monkeypatch):
    """The pinned version resolves to something other than the exact
    business-journey DeepSeek config — `select_journey_model_config` itself
    is what enforces that; the mismatch is simulated at the same seam."""
    version_id = str(uuid.uuid4())

    def _reject(vid, db=None):
        raise ModelConfigurationError(f"PROVIDER_MISMATCH: openai")

    monkeypatch.setattr(model_config_selector, "select_journey_model_config", _reject)
    return version_id


def _context(*, model_config_version_id: str, extra: dict) -> TurnRuntimeContext:
    return TurnRuntimeContext(
        turn_id=str(uuid.uuid4()), session_id=str(uuid.uuid4()), agent_id=str(uuid.uuid4()),
        agent_version_id=str(uuid.uuid4()), release_id="release-journey-001",
        model_config_version_id=model_config_version_id, model_name=MODEL_ID,
        user_message="Which suppliers are below safety stock?", extra=extra,
    )


def _run(runtime: LangGraphRuntime, context: TurnRuntimeContext):
    return asyncio.run(runtime.start_turn(context))


class _FakeGateway:
    """Duck-typed stand-in for `ToolGateway` — `gateway` is an injectable
    constructor field on `LangGraphRuntime`, exactly like `caller`."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests: list[object] = []

    def execute(self, request, *, ontology_id: str):
        self.requests.append(request)
        return SimpleNamespace(outcome="allowed", correlation_id="tool:fake", payload=self.payload)


def test_business_journey_turn_uses_deepseek_vision_caller_for_both_completions(
    db, deepseek_transport, deepseek_api_key, pinned_model_config_version_id,
):
    deepseek_transport.queue.append({"tool_call": None, "answer": None})
    deepseek_transport.queue.append({
        "answer": "No suppliers are below safety stock.",
        "entities": [], "relations": [], "rules": [], "actions": [], "citations": [],
    })
    ledger = ModelCallLedger()
    context = _context(
        model_config_version_id=pinned_model_config_version_id,
        extra={
            "user_id": str(uuid.uuid4()),
            "business_journey": {"run_id": RUN_ID, "journey_id": JOURNEY_ID, "ledger": ledger},
        },
    )
    runtime = LangGraphRuntime(db=db, gateway=_FakeGateway({}), max_tool_rounds=1)
    events = _run(runtime, context)

    model_call_events = [e for e in events if e.event_type == "model_call"]
    assert [e.payload["call_kind"] for e in model_call_events] == ["agent_initial", "agent_final"]
    assert [e.payload["logical_call_index"] for e in model_call_events] == [2, 3]
    assert all(e.payload["model_caller"] == "DeepSeekVisionCaller" for e in model_call_events)
    assert all(e.payload["model_origin"] == OFFICIAL_ORIGIN for e in model_call_events)
    assert all(e.payload["observed_model"] == MODEL_ID for e in model_call_events)
    # The REAL value a live `/models` call observed, not a hardcoded
    # constant (Important Finding #11) — and exactly one preflight call was
    # made for the whole turn, before either completion.
    assert all(e.payload["preflight_model_id"] == MODEL_ID for e in model_call_events)
    assert deepseek_transport.preflight_calls == 1
    assert model_call_events[0].payload["correlation_id"] == f"{RUN_ID}:{JOURNEY_ID}:agent_initial:2"
    assert model_call_events[1].payload["correlation_id"] == f"{RUN_ID}:{JOURNEY_ID}:agent_final:3"

    final_event = next(e for e in events if e.event_type == "final_response")
    assert final_event.payload["message"] == "No suppliers are below safety stock."
    assert any(e.event_type == "turn_succeeded" for e in events)

    totals = ledger.totals(RUN_ID, JOURNEY_ID)
    assert totals["logical_model_calls"] == 2


def test_business_journey_turn_requires_a_successful_models_preflight_before_either_completion(
    db, deepseek_transport, deepseek_api_key, pinned_model_config_version_id,
):
    """Important Finding #11: the `/models` probe must be genuinely
    enforced, not a hardcoded value that can never fail. Simulating a
    `/models` response that does not include MODEL_ID must fail the turn
    before EITHER completion is attempted."""
    deepseek_transport.models_available = ["some-other-model"]
    deepseek_transport.queue.append({"tool_call": None, "answer": None})
    deepseek_transport.queue.append({"answer": "unreachable", "entities": [], "relations": [],
                                     "rules": [], "actions": [], "citations": []})
    context = _context(
        model_config_version_id=pinned_model_config_version_id,
        extra={
            "user_id": str(uuid.uuid4()),
            "business_journey": {"run_id": RUN_ID, "journey_id": JOURNEY_ID},
        },
    )
    runtime = LangGraphRuntime(db=db, gateway=_FakeGateway({}), max_tool_rounds=1)
    events = _run(runtime, context)

    failed = next(e for e in events if e.event_type == "turn_failed")
    assert failed.payload["error_code"] == "MODEL_PREFLIGHT_FAILED"
    assert not any(e.event_type == "model_call" for e in events)
    assert len(deepseek_transport.queue) == 2  # neither queued completion was ever consumed


def test_business_journey_turn_makes_exactly_one_governed_tool_call_when_the_model_asks(
    db, deepseek_transport, deepseek_api_key, pinned_model_config_version_id,
):
    deepseek_transport.queue.append({
        "tool_call": {"descriptor_id": "query:ontology-001", "query": "below safety stock"},
        "answer": None,
    })
    deepseek_transport.queue.append({
        "answer": "Supplier MAT001 is below safety stock.",
        "entities": ["Supplier"], "relations": [], "rules": [], "actions": [], "citations": [],
    })
    gateway = _FakeGateway({"items": [{"id": "MAT001"}]})
    context = _context(
        model_config_version_id=pinned_model_config_version_id,
        extra={
            "user_id": str(uuid.uuid4()),
            "business_journey": {"run_id": RUN_ID, "journey_id": JOURNEY_ID},
            "ontology_tool_selection": [{"ontology_id": "ontology-001", "selected_tools": []}],
        },
    )
    runtime = LangGraphRuntime(db=db, gateway=gateway, max_tool_rounds=1)
    events = _run(runtime, context)

    tool_events = [e for e in events if e.event_type == "tool_executed"]
    assert len(tool_events) == 1
    assert tool_events[0].payload["outcome"] == "allowed"
    assert len(gateway.requests) == 1

    final_event = next(e for e in events if e.event_type == "final_response")
    assert final_event.payload["message"] == "Supplier MAT001 is below safety stock."


def test_business_journey_turn_rejects_a_pinned_config_that_is_not_deepseek(
    db, deepseek_api_key, mismatched_model_config_version_id,
):
    context = _context(
        model_config_version_id=mismatched_model_config_version_id,
        extra={
            "user_id": str(uuid.uuid4()),
            "business_journey": {"run_id": RUN_ID, "journey_id": JOURNEY_ID},
        },
    )
    runtime = LangGraphRuntime(db=db, gateway=_FakeGateway({}), max_tool_rounds=1)
    events = _run(runtime, context)

    failed = next(e for e in events if e.event_type == "turn_failed")
    assert failed.payload["error_code"] == "MODEL_CONFIG_INVALID"
    assert not any(e.event_type == "model_call" for e in events)


def test_business_journey_turn_requires_the_deepseek_api_key_env_var(
    db, deepseek_transport, pinned_model_config_version_id, monkeypatch,
):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    context = _context(
        model_config_version_id=pinned_model_config_version_id,
        extra={
            "user_id": str(uuid.uuid4()),
            "business_journey": {"run_id": RUN_ID, "journey_id": JOURNEY_ID},
        },
    )
    runtime = LangGraphRuntime(db=db, gateway=_FakeGateway({}), max_tool_rounds=1)
    events = _run(runtime, context)

    failed = next(e for e in events if e.event_type == "turn_failed")
    assert failed.payload["error_code"] == "DEEPSEEK_API_KEY_REQUIRED"


def _sha256_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_business_journey_turn_persists_real_tool_evidence_governed_plan_creation_can_read(
    db, deepseek_transport, deepseek_api_key, pinned_model_config_version_id,
):
    """Closes the exact gap Critical Finding #1 identified: `LangGraphRuntime`
    must persist a REAL `agent_tool_executions` row for a business-journey
    turn's governed query call, and `create_governed_plan_from_turn` must be
    able to find it — not a row `test_runtime_turn_plans.py` hand-seeded.
    This test runs the real turn through `LangGraphRuntime.start_turn`
    (writing the tool-execution row for real), separately marks the turn
    'succeeded' with a real response message (the worker's own job,
    untouched by this task), and then calls `create_governed_plan_from_turn`
    against the SAME database with NO hand-seeded tool-evidence row."""
    user = User(
        id=str(uuid.uuid4()), username="tp-e2e-user", email="tp-e2e@example.invalid",
        password_hash="x", role="editor", security_domain_id="00000000-0000-0000-0000-000000000001",
    )
    db.add(user)
    db.commit()

    project = OntologyProject(
        id=str(uuid.uuid4()), name="tp-e2e-ontology", domain="test",
        created_by=user.id, security_domain_id="00000000-0000-0000-0000-000000000001",
    )
    db.add(project)
    db.flush()
    entity = Entity(id=str(uuid.uuid4()), ontology_id=project.id, name_cn="目标实体")
    db.add(entity)
    db.flush()
    release_id = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO ontology_releases "
        "(id, ontology_id, version_no, version, manifest_bytes, manifest_projection, "
        "schema_hash, status, created_by, created_at) "
        "VALUES (:id, :oid, 1, 'v1', :mb, :proj, :sh, 'published', :cb, CURRENT_TIMESTAMP)"
    ), {
        "id": release_id, "oid": project.id, "mb": b"manifest",
        "proj": json.dumps({"entities": [{"id": entity.id}]}),
        "sh": b"schema-hash-0000000000000000000000", "cb": user.id,
    })
    project.latest_published_release_id = release_id
    db.commit()
    instance = EntityInstance(
        id=str(uuid.uuid4()), entity_id=entity.id, ontology_id=project.id,
        row_identity="row-1", row_data={"name": "MAT001"},
    )
    db.add(instance)
    db.commit()

    agent_id = str(uuid.uuid4())
    agent_version_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO agents (id, visibility, status, owner_id, active_version_id, created_at, updated_at) "
        "VALUES (:id, 'private', 'active', :owner, :version, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": agent_id, "owner": user.id, "version": agent_version_id})
    db.execute(text(
        "INSERT INTO agent_ontology_bindings (id, agent_version_id, ontology_id, capabilities, allowlists, created_at) "
        "VALUES (:id, :version, :ontology, '[]', '{}', CURRENT_TIMESTAMP)"
    ), {"id": str(uuid.uuid4()), "version": agent_version_id, "ontology": project.id})
    db.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES (:id, :agent, :owner, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": session_id, "agent": agent_id, "owner": user.id})
    db.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, dispatch_generation, created_at, updated_at) "
        "VALUES (:id, :sid, 'running', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": turn_id, "sid": session_id})
    db.commit()

    deepseek_transport.queue.append({
        "tool_call": {"descriptor_id": f"query:{project.id}", "query": "below safety stock"},
        "answer": None,
    })
    response_content = "Supplier MAT001 is below safety stock."
    deepseek_transport.queue.append({
        "answer": response_content,
        "entities": ["Supplier"], "relations": [], "rules": [], "actions": [], "citations": [],
    })

    class _RealishGateway:
        """Executes against the real `entity_instances` row seeded above,
        instead of a canned payload — proving the persisted tool-execution
        row's `result_hash` reflects a real query outcome."""

        def execute(self, request, *, ontology_id: str):
            rows = db.query(EntityInstance).filter(EntityInstance.ontology_id == ontology_id).all()
            payload = {"items": [{"id": r.id, "row_data": r.row_data} for r in rows]}
            return SimpleNamespace(outcome="allowed", correlation_id="tool:real", payload=payload)

    context = TurnRuntimeContext(
        turn_id=turn_id, session_id=session_id, agent_id=agent_id, agent_version_id=agent_version_id,
        release_id=release_id, model_config_version_id=pinned_model_config_version_id, model_name=MODEL_ID,
        user_message="Which suppliers are below safety stock?",
        extra={
            "user_id": user.id,
            "business_journey": {"run_id": RUN_ID, "journey_id": JOURNEY_ID},
            "ontology_tool_selection": [{"ontology_id": project.id, "selected_tools": []}],
        },
    )
    runtime = LangGraphRuntime(db=db, gateway=_RealishGateway(), max_tool_rounds=1)
    events = _run(runtime, context)

    tool_events = [e for e in events if e.event_type == "tool_executed"]
    assert len(tool_events) == 1
    tool_execution_id = tool_events[0].payload["tool_execution_id"]

    # Confirm the row is REAL — read it back independently of the runtime.
    persisted = db.execute(text(
        "SELECT id, turn_id, status, result_hash FROM agent_tool_executions WHERE id = :id"
    ), {"id": tool_execution_id}).mappings().one()
    assert persisted["turn_id"] == turn_id
    assert persisted["status"] == "succeeded"
    assert persisted["result_hash"]

    # The worker's own job (untouched by this task): finalize the turn with
    # a real response message once the transcript is complete.
    response_message_id = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO agent_messages (id, session_id, turn_id, role, ordinal, content, created_at) "
        "VALUES (:id, :sid, :turn, 'assistant', 1, :content, CURRENT_TIMESTAMP)"
    ), {"id": response_message_id, "sid": session_id, "turn": turn_id, "content": response_content})
    db.execute(text(
        "UPDATE agent_turns SET status = 'succeeded', response_message_id = :rmid WHERE id = :id"
    ), {"rmid": response_message_id, "id": turn_id})
    db.commit()

    tool_result_hash = persisted["result_hash"]
    digest = _sha256_json({
        "turn_id": turn_id, "tool_execution_id": tool_execution_id, "tool_result_hash": tool_result_hash,
        "final_response_hash": _sha256_text(response_content), "branch": "approved",
        "target_fixture_id": instance.id,
    })
    plan = create_governed_plan_from_turn(
        db, turn_id=turn_id, branch="approved", target_fixture_id=instance.id,
        idempotency_key=f"key-{uuid.uuid4()}", payload_digest=digest, actor_user_id=user.id,
    )
    assert plan.status == "pending"


def _seed_automatic_action_fixture(db):
    """Real user/ontology/release/instance/agent/session/turn rows an
    automatic-action test needs — shared by the accept and reject paths."""
    user = User(
        id=str(uuid.uuid4()), username=f"tp-auto-{uuid.uuid4().hex[:8]}",
        email=f"tp-auto-{uuid.uuid4().hex[:8]}@example.invalid",
        password_hash="x", role="editor", security_domain_id="00000000-0000-0000-0000-000000000001",
    )
    db.add(user)
    db.commit()

    project = OntologyProject(
        id=str(uuid.uuid4()), name="tp-auto-ontology", domain="test",
        created_by=user.id, security_domain_id="00000000-0000-0000-0000-000000000001",
    )
    db.add(project)
    db.flush()
    entity = Entity(id=str(uuid.uuid4()), ontology_id=project.id, name_cn="目标实体")
    db.add(entity)
    db.flush()
    release_id = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO ontology_releases "
        "(id, ontology_id, version_no, version, manifest_bytes, manifest_projection, "
        "schema_hash, status, created_by, created_at) "
        "VALUES (:id, :oid, 1, 'v1', :mb, :proj, :sh, 'published', :cb, CURRENT_TIMESTAMP)"
    ), {
        "id": release_id, "oid": project.id, "mb": b"manifest",
        "proj": json.dumps({"entities": [{"id": entity.id}]}),
        "sh": b"schema-hash-0000000000000000000000", "cb": user.id,
    })
    project.latest_published_release_id = release_id
    db.commit()
    instance = EntityInstance(
        id=str(uuid.uuid4()), entity_id=entity.id, ontology_id=project.id,
        row_identity="row-1", row_data={"name": "MAT001"},
    )
    db.add(instance)
    db.commit()

    agent_id = str(uuid.uuid4())
    agent_version_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO agents (id, visibility, status, owner_id, active_version_id, created_at, updated_at) "
        "VALUES (:id, 'private', 'active', :owner, :version, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": agent_id, "owner": user.id, "version": agent_version_id})
    db.execute(text(
        "INSERT INTO agent_ontology_bindings (id, agent_version_id, ontology_id, capabilities, allowlists, created_at) "
        "VALUES (:id, :version, :ontology, '[]', '{}', CURRENT_TIMESTAMP)"
    ), {"id": str(uuid.uuid4()), "version": agent_version_id, "ontology": project.id})
    db.execute(text(
        "INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at) "
        "VALUES (:id, :agent, :owner, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": session_id, "agent": agent_id, "owner": user.id})
    db.execute(text(
        "INSERT INTO agent_turns (id, session_id, status, dispatch_generation, created_at, updated_at) "
        "VALUES (:id, :sid, 'running', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": turn_id, "sid": session_id})
    db.commit()
    return SimpleNamespace(
        user=user, project=project, entity=entity, release_id=release_id, instance=instance,
        agent_id=agent_id, agent_version_id=agent_version_id, session_id=session_id, turn_id=turn_id,
    )


def test_business_journey_turn_executes_the_automatic_low_risk_action_with_a_real_receipt(
    db, deepseek_transport, deepseek_api_key, pinned_model_config_version_id,
):
    """Closes Critical Finding #3: the turn's one low-risk action must run
    automatically (no approval gate) with a REAL receipt — not the event
    payload shape only the test fake previously supplied. This runs the real
    `LangGraphRuntime.start_turn`, whose final completion names the fixture
    row to act on, and then reads back — independently of the runtime — the
    real `EntityInstance.row_data` mutation and the real
    `governance_audit_logs` row the automatic action must have written."""
    seed = _seed_automatic_action_fixture(db)
    instance = seed.instance
    original_revision = instance.revision

    deepseek_transport.queue.append({"tool_call": None, "answer": None})
    deepseek_transport.queue.append({
        "answer": "Supplier MAT001 flagged for review.",
        "entities": ["Supplier"], "relations": [], "rules": [], "actions": [], "citations": [],
        "automatic_action": {"target_fixture_id": instance.id, "action": JOURNEY_LOW_RISK_ACTION},
    })

    context = TurnRuntimeContext(
        turn_id=seed.turn_id, session_id=seed.session_id, agent_id=seed.agent_id,
        agent_version_id=seed.agent_version_id, release_id=seed.release_id,
        model_config_version_id=pinned_model_config_version_id, model_name=MODEL_ID,
        user_message="Flag MAT001 for review.",
        extra={
            "user_id": seed.user.id,
            "business_journey": {"run_id": RUN_ID, "journey_id": JOURNEY_ID},
        },
    )
    runtime = LangGraphRuntime(db=db, gateway=_FakeGateway({}), max_tool_rounds=1)
    events = _run(runtime, context)

    final_event = next(e for e in events if e.event_type == "final_response")
    receipt_id = final_event.payload["receipt_id"]
    sandbox_receipt_id = final_event.payload["sandbox_receipt_id"]
    audit_event_id = final_event.payload["audit_event_id"]
    assert receipt_id and sandbox_receipt_id and audit_event_id
    assert final_event.payload["automatic_action"] == JOURNEY_LOW_RISK_ACTION
    assert any(e.event_type == "turn_succeeded" for e in events)

    # Read back the real mutation, independently of the runtime.
    db.expire(instance)
    assert instance.row_data.get("_automatic_action_applied") == JOURNEY_LOW_RISK_ACTION
    assert instance.revision == original_revision + 1

    # Read back the real audit event, independently of the runtime.
    audit_row = db.execute(text(
        "SELECT operation, decision, actor_user_id FROM governance_audit_logs WHERE id = :id"
    ), {"id": audit_event_id}).mappings().one()
    assert audit_row["operation"] == "runtime.automatic_action.execute"
    assert audit_row["decision"] == "automatic"
    assert audit_row["actor_user_id"] == seed.user.id


def test_business_journey_turn_rejects_an_automatic_action_that_does_not_match_the_manifest(
    db, deepseek_transport, deepseek_api_key, pinned_model_config_version_id,
):
    """Second review round, Important Finding: nothing structurally stopped
    an unconstrained model from proposing an `automatic_action.action` other
    than the journey's exact `low_risk_action` — which `verify_journey`
    would then reject anyway (deterministically, for every real run). This
    simulates a provider that ignores/violates the (now-enum-constrained)
    response schema and proposes an out-of-manifest action string, and
    confirms the turn fails closed — no execution, no mutation, no receipt —
    rather than silently accepting arbitrary text."""
    seed = _seed_automatic_action_fixture(db)
    instance = seed.instance
    original_revision = instance.revision
    original_row_data = dict(instance.row_data)

    deepseek_transport.queue.append({"tool_call": None, "answer": None})
    deepseek_transport.queue.append({
        "answer": "Supplier MAT001 flagged for review.",
        "entities": ["Supplier"], "relations": [], "rules": [], "actions": [], "citations": [],
        "automatic_action": {"target_fixture_id": instance.id, "action": "delete_everything"},
    })

    context = TurnRuntimeContext(
        turn_id=seed.turn_id, session_id=seed.session_id, agent_id=seed.agent_id,
        agent_version_id=seed.agent_version_id, release_id=seed.release_id,
        model_config_version_id=pinned_model_config_version_id, model_name=MODEL_ID,
        user_message="Flag MAT001 for review.",
        extra={
            "user_id": seed.user.id,
            "business_journey": {"run_id": RUN_ID, "journey_id": JOURNEY_ID},
        },
    )
    runtime = LangGraphRuntime(db=db, gateway=_FakeGateway({}), max_tool_rounds=1)
    events = _run(runtime, context)

    failed = next(e for e in events if e.event_type == "turn_failed")
    assert failed.payload["error_code"] == "AUTOMATIC_ACTION_INVALID"
    assert not any(e.event_type == "final_response" for e in events)

    # No mutation, no receipt, no audit event — rejected before execution.
    db.expire(instance)
    assert instance.row_data == original_row_data
    assert instance.revision == original_revision
    audit_count = db.execute(text(
        "SELECT COUNT(*) AS n FROM governance_audit_logs WHERE operation = 'runtime.automatic_action.execute'"
    )).mappings().one()["n"]
    assert audit_count == 0
