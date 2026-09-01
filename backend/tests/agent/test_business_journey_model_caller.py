"""TDD tests for `DeepSeekVisionCaller`: shared config/ledger across all
three completion kinds, atomic retry accounting (a retry never double-counts
the logical call), rejection of any provider/model/config drift, and
`select_journey_model_config`'s no-fallback drift rejection.

Every test here injects a fake transport by reaching into a caller's
private ``_client`` attribute after construction -- `DeepSeekVisionCaller`'s
public constructor (`api_key`, `model_config`, `ledger`, `timeout_seconds`)
has no transport parameter, so the real gate can never accept one.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.services.model_callers.deepseek_vision import DeepSeekVisionCaller
from app.services.model_config_selector import select_journey_model_config
from evals.business_journeys.contracts import (
    MODEL_ID,
    OFFICIAL_ORIGIN,
    InputPart,
    JourneyModelContext,
    ModelCallLedger,
    ModelConfigurationError,
    sha256_text,
)
from evals.business_journeys.deepseek_client import DeepSeekVisionClient


def _chat_response(model: str = MODEL_ID) -> httpx.Response:
    body = {"model": model, "choices": [{"message": {"content": json.dumps({"answer": "ok"})}}]}
    return httpx.Response(200, json=body)


class _FakeTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self._post_queue: list[object] = []

    def queue_429_then_success(self, *, model: str = MODEL_ID) -> None:
        self._post_queue = [httpx.Response(429, json={"error": "rate_limited"}), _chat_response(model)]

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": MODEL_ID}]})
        if not self._post_queue:
            return _chat_response()
        item = self._post_queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _RecordingCaller:
    """Wraps a real `DeepSeekVisionCaller`, recording each call for assertions."""

    def __init__(self, inner: DeepSeekVisionCaller, runtime: "_MockRuntime") -> None:
        self._inner = inner
        self._runtime = runtime

    def complete(self, context: JourneyModelContext, parts, *, response_schema):
        response = self._inner.complete(context, parts, response_schema=response_schema)
        self._runtime.calls.append(context.call_kind)
        self._runtime.records.append(
            SimpleNamespace(
                model_id=response.model,
                origin=OFFICIAL_ORIGIN,
                model_config_version_id=context.model_config_version_id,
                correlation_id=context.correlation_id,
            )
        )
        return response


class _MockRuntime:
    """Test-only harness wiring a real `DeepSeekVisionCaller` + `ModelCallLedger`
    to a fake transport, so retry/ledger/drift behavior is exercised for real."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(
            version_id="cfg-1",
            provider="deepseek",
            model_id=MODEL_ID,
            origin=OFFICIAL_ORIGIN,
            behavior_hash="hash-1",
            frozen_at="2026-08-27T00:00:00Z",
        )
        self.ledger_impl = ModelCallLedger()
        self.transport = _FakeTransport()
        self.calls: list[str] = []
        self.records: list[SimpleNamespace] = []
        self.context = self.run_context("run-default", "supply_chain", "cfg-1")

    def run_context(self, run_id: str, journey_id: str, model_config_version_id: str) -> JourneyModelContext:
        return JourneyModelContext(
            run_id=run_id,
            journey_id=journey_id,
            model_config_version_id=model_config_version_id,
            model_id=MODEL_ID,
            call_kind="ontology",
            logical_call_index=1,
            correlation_id=f"{run_id}:{journey_id}:ontology:1",
        )

    def caller(self, context: JourneyModelContext | None = None) -> _RecordingCaller:
        inner = DeepSeekVisionCaller("runtime/runtime", self.config, self.ledger_impl)
        inner._client = DeepSeekVisionClient("runtime/runtime", transport=self.transport)
        return _RecordingCaller(inner, self)

    def queue_429_then_success(self) -> None:
        self.transport.queue_429_then_success()

    @property
    def ledger(self) -> SimpleNamespace:
        totals = self.ledger_impl.totals(self.context.run_id, self.context.journey_id)
        return SimpleNamespace(**totals)


@pytest.fixture
def mock_runtime() -> _MockRuntime:
    return _MockRuntime()


def test_all_completion_kinds_share_the_same_caller_config_and_ledger(mock_runtime):
    context = mock_runtime.run_context("run-1", "supply_chain", "cfg-1")
    caller = mock_runtime.caller(context)
    for kind, index in (("ontology", 1), ("agent_initial", 2), ("agent_final", 3)):
        caller.complete(
            context.for_call(kind, index),
            [InputPart("text", "text/plain", "synthetic", sha256_text("synthetic"))],
            response_schema={"type": "object"},
        )
    assert mock_runtime.calls == ["ontology", "agent_initial", "agent_final"]
    assert all(call.model_id == MODEL_ID for call in mock_runtime.records)
    assert all(call.origin == OFFICIAL_ORIGIN for call in mock_runtime.records)
    assert all(call.model_config_version_id == "cfg-1" for call in mock_runtime.records)
    assert [call.correlation_id for call in mock_runtime.records] == [
        "run-1:supply_chain:ontology:1",
        "run-1:supply_chain:agent_initial:2",
        "run-1:supply_chain:agent_final:3",
    ]


def test_retry_does_not_increment_logical_call_twice_and_ledger_is_atomic(mock_runtime):
    mock_runtime.queue_429_then_success()
    response = mock_runtime.caller().complete(
        mock_runtime.context.for_call("agent_initial", 2),
        [],
        response_schema={"type": "object"},
    )
    assert response.http_attempts == 2
    assert mock_runtime.ledger.logical_model_calls == 1
    assert mock_runtime.ledger.http_attempts == 2


def test_runtime_rejects_provider_model_or_config_fallback(mock_runtime):
    mock_runtime.config.model_id = "deepseek-v4-flash"
    with pytest.raises(ModelConfigurationError, match="MODEL_ID_MISMATCH"):
        mock_runtime.caller().complete(
            mock_runtime.context.for_call("agent_initial", 2),
            [],
            response_schema={"type": "object"},
        )


def test_duplicate_logical_call_index_is_rejected(mock_runtime):
    context = mock_runtime.context.for_call("agent_initial", 2)
    mock_runtime.caller().complete(context, [], response_schema={"type": "object"})
    with pytest.raises(ModelConfigurationError):
        mock_runtime.caller().complete(context, [], response_schema={"type": "object"})


def test_fourth_logical_call_exceeds_journey_budget(mock_runtime):
    for index, kind in ((1, "ontology"), (2, "agent_initial"), (3, "agent_final")):
        mock_runtime.caller().complete(
            mock_runtime.context.for_call(kind, index), [], response_schema={"type": "object"}
        )
    with pytest.raises(ModelConfigurationError):
        mock_runtime.ledger_impl.begin_logical_call(
            JourneyModelContext(
                run_id=mock_runtime.context.run_id,
                journey_id=mock_runtime.context.journey_id,
                model_config_version_id="cfg-1",
                model_id=MODEL_ID,
                call_kind="agent_final",
                logical_call_index=3,
                correlation_id="extra",
            )
        )


# ---------------------------------------------------------------------------
# select_journey_model_config: no provider/model/origin fallback.
# ---------------------------------------------------------------------------


class _FakeMappingResult:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


class _FakeExecuteResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return _FakeMappingResult(self._row)


class _FakeDb:
    def __init__(self, row):
        self._row = row

    def execute(self, _statement, _params=None):
        return _FakeExecuteResult(self._row)


def test_select_journey_model_config_returns_immutable_version_on_exact_match():
    row = {
        "id": "cfg-1",
        "provider": "deepseek",
        "api_base": OFFICIAL_ORIGIN,
        "model_contract": [{"provider_model_revision": MODEL_ID}],
        "behavior_hash": "hash-1",
        "created_at": "2026-08-27T00:00:00Z",
    }
    config = select_journey_model_config("cfg-1", db=_FakeDb(row))
    assert config.version_id == "cfg-1"
    assert config.provider == "deepseek"
    assert config.model_id == MODEL_ID
    assert config.origin == OFFICIAL_ORIGIN
    assert config.behavior_hash == "hash-1"


def test_select_journey_model_config_rejects_model_drift():
    row = {
        "id": "cfg-1",
        "provider": "deepseek",
        "api_base": OFFICIAL_ORIGIN,
        "model_contract": [{"provider_model_revision": "deepseek-v4-flash"}],
        "behavior_hash": "hash-1",
        "created_at": "2026-08-27T00:00:00Z",
    }
    with pytest.raises(ModelConfigurationError, match="MODEL_ID_MISMATCH"):
        select_journey_model_config("cfg-1", db=_FakeDb(row))


def test_select_journey_model_config_rejects_provider_drift():
    row = {
        "id": "cfg-1",
        "provider": "openai",
        "api_base": OFFICIAL_ORIGIN,
        "model_contract": [{"provider_model_revision": MODEL_ID}],
        "behavior_hash": "hash-1",
        "created_at": "2026-08-27T00:00:00Z",
    }
    with pytest.raises(ModelConfigurationError, match="PROVIDER_MISMATCH"):
        select_journey_model_config("cfg-1", db=_FakeDb(row))


def test_select_journey_model_config_rejects_origin_drift():
    row = {
        "id": "cfg-1",
        "provider": "deepseek",
        "api_base": "https://evil.example.invalid",
        "model_contract": [{"provider_model_revision": MODEL_ID}],
        "behavior_hash": "hash-1",
        "created_at": "2026-08-27T00:00:00Z",
    }
    with pytest.raises(ModelConfigurationError, match="ORIGIN_MISMATCH"):
        select_journey_model_config("cfg-1", db=_FakeDb(row))


def test_select_journey_model_config_rejects_missing_version():
    with pytest.raises(ModelConfigurationError, match="MODEL_CONFIG_VERSION_NOT_FOUND"):
        select_journey_model_config("missing", db=_FakeDb(None))
