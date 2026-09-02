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
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

import app.services.model_config_selector as model_config_selector
from app.runtime.langgraph_runtime import LangGraphRuntime, RuntimeModelError
from app.runtime.protocol import TurnRuntimeContext
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


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(DeepSeekVisionClient, "_sleep", staticmethod(lambda seconds: None))


@pytest.fixture
def deepseek_transport(monkeypatch):
    """Installs a fake DeepSeek transport and returns the mutable queue of
    structured JSON bodies it will hand back, in call order."""
    queue: list[dict] = []
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [{"id": MODEL_ID}]})
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
    return SimpleNamespace(queue=queue, calls=calls)


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
    assert model_call_events[0].payload["correlation_id"] == f"{RUN_ID}:{JOURNEY_ID}:agent_initial:2"
    assert model_call_events[1].payload["correlation_id"] == f"{RUN_ID}:{JOURNEY_ID}:agent_final:3"

    final_event = next(e for e in events if e.event_type == "final_response")
    assert final_event.payload["message"] == "No suppliers are below safety stock."
    assert any(e.event_type == "turn_succeeded" for e in events)

    totals = ledger.totals(RUN_ID, JOURNEY_ID)
    assert totals["logical_model_calls"] == 2


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
