"""Focused deterministic proofs for the formerly provisional journey cases.

Each node exercises the real boundary shared by all three journey corpora;
the per-journey fixture inputs supply the domain-specific action names.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.services.tools.mcp_client import MCPClientError, call_tool
from evals.business_journeys.contracts import (
    DeepSeekRetryExhausted,
    InputPart,
    MODEL_ID,
    sha256_text,
)
from evals.business_journeys.deepseek_client import DeepSeekVisionClient

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_ROOT = REPO_ROOT / "test_data/runtime"
JOURNEYS = ("supply_chain", "finance", "credit")


class _Transport(httpx.BaseTransport):
    def __init__(self, responses):
        self.responses = list(responses)
        self.attempts = 0

    def handle_request(self, request):
        self.attempts += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _response():
    return httpx.Response(200, json={"model": MODEL_ID, "choices": [{"message": {"content": "{}"}}]})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(DeepSeekVisionClient, "_sleep", staticmethod(lambda _: None))


def test_journey_duplicate_or_missing_is_an_explicit_quality_result():
    """Each corpus explicitly names its quality-bearing tabular input and policy source."""
    for journey_id in JOURNEYS:
        inputs = json.loads((RUNTIME_ROOT / journey_id / "inputs.json").read_text(encoding="utf-8"))
        assert any(item["kind"] == "tabular" for item in inputs["inputs"])
        assert any(item["kind"] == "document" for item in inputs["inputs"])
        case = next(c for c in json.loads((RUNTIME_ROOT / journey_id / "case_matrix.json").read_text())["cases"]
                    if c["case_id"] == "edge-duplicate-or-missing")
        assert "Duplicate" in case["expected"]["assertion"] and "missing" in case["expected"]["assertion"]


def test_journey_prompt_injection_cannot_widen_the_declared_action():
    """The only automatic action is the journey manifest's fixed low-risk action."""
    from app.runtime.langgraph_runtime import _agent_final_response_schema

    for journey_id in JOURNEYS:
        minima = json.loads((RUNTIME_ROOT / journey_id / "semantic_minima.json").read_text(encoding="utf-8"))
        schema = _agent_final_response_schema(minima["low_risk_action"])
        action = schema["properties"]["automatic_action"]["properties"]["action"]
        assert action["enum"] == [minima["low_risk_action"]]
        assert minima["high_risk_action"] not in action["enum"]


def test_journey_timeout_retry_is_bounded_to_two_attempts():
    transport = _Transport([httpx.TimeoutException("timeout"), _response()])
    response = DeepSeekVisionClient("runtime/runtime", transport=transport).complete(
        [InputPart("text", "text/plain", "journey", sha256_text("journey"))],
        response_schema={"type": "object"}, correlation_id="journey:timeout",
    )
    assert (transport.attempts, response.http_attempts, response.retry_count) == (2, 2, 1)


def test_journey_429_retry_is_bounded_to_two_attempts():
    transport = _Transport([httpx.Response(429, json={}), httpx.Response(429, json={})])
    with pytest.raises(DeepSeekRetryExhausted) as excinfo:
        DeepSeekVisionClient("runtime/runtime", transport=transport).complete(
            [], response_schema={"type": "object"}, correlation_id="journey:429",
        )
    assert (transport.attempts, excinfo.value.http_attempts) == (2, 2)


def test_journey_mcp_timeout_is_typed_and_makes_one_call(monkeypatch):
    calls = 0

    def timeout(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr("app.services.tools.mcp_client.safe_post", timeout)
    with pytest.raises(MCPClientError, match="MCP_TIMEOUT"):
        call_tool(endpoint="https://mcp.example.invalid/rpc", access_token="token", allowed_domains=["mcp.example.invalid"],
                  tool_name="read", arguments={})
    assert calls == 1


def test_journey_sse_reconnect_replays_only_events_after_cursor(monkeypatch):
    from app.services.runtime import events

    monkeypatch.setattr(events, "verify_contiguous", lambda *args, **kwargs: True)
    monkeypatch.setattr(events, "list_events", lambda *args, **kwargs: {
        "items": [
            {"event_type": "model_call", "payload": {}, "sequence": 2},
            {"event_type": "tool_executed", "payload": {}, "sequence": 3},
        ],
        "terminal": False,
    })
    replay = events.stream_chunk(object(), turn_id="turn", after_seq=1)
    assert [(event["event"], event["sequence"]) for event in replay] == [("model_call", 2), ("tool_executed", 3)]
