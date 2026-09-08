"""TDD contract tests for the exact-model, official-origin DeepSeek client.

Every test here injects a fake ``httpx.BaseTransport`` (``mock_http``); no
test in this file makes a real network call or accepts a real API key.
"""
from __future__ import annotations

import inspect
import json

import httpx
import pytest

from evals.business_journeys.contracts import (
    MODEL_ID,
    DeepSeekEndpointError,
    DeepSeekProviderError,
    DeepSeekRetryExhausted,
    InputPart,
    ModelConfigurationError,
    sha256_text,
)
from evals.business_journeys.deepseek_client import DeepSeekVisionClient, validate_official_url


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The retry loop does a real bounded exponential backoff; keep tests
    fast by swapping in a no-op delay instead of actually sleeping."""
    monkeypatch.setattr(DeepSeekVisionClient, "_sleep", staticmethod(lambda seconds: None))


def _chat_response(model: str = MODEL_ID) -> httpx.Response:
    body = {"model": model, "choices": [{"message": {"content": json.dumps({"answer": "ok"})}}]}
    return httpx.Response(200, json=body)


class FakeDeepSeekTransport(httpx.BaseTransport):
    """A queueable fake for the two official DeepSeek endpoints."""

    def __init__(self) -> None:
        self._get_responses: dict[str, httpx.Response] = {}
        self._post_queue: list[object] = []
        self.post_attempts = 0
        self.last_request: httpx.Request | None = None

    def get(self, path: str, *, json: object) -> None:
        self._get_responses[path] = httpx.Response(200, json=json)

    def queue_timeout_then_success(self, *, model: str = MODEL_ID) -> None:
        self._post_queue = [httpx.TimeoutException("timeout"), _chat_response(model)]

    def queue_429_then_429(self) -> None:
        self._post_queue = [
            httpx.Response(429, json={"error": "rate_limited"}),
            httpx.Response(429, json={"error": "rate_limited"}),
        ]

    def queue_429_then_success(self, *, model: str = MODEL_ID) -> None:
        self._post_queue = [httpx.Response(429, json={"error": "rate_limited"}), _chat_response(model)]

    def queue_responses(self, *items: object) -> None:
        self._post_queue = list(items)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            response = self._get_responses.get(request.url.path)
            return response if response is not None else httpx.Response(200, json={"data": [{"id": MODEL_ID}]})
        if request.method == "POST":
            self.post_attempts += 1
            self.last_request = request
            if not self._post_queue:
                return _chat_response()
            item = self._post_queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        raise AssertionError(f"unexpected method {request.method}")


@pytest.fixture
def mock_http() -> FakeDeepSeekTransport:
    return FakeDeepSeekTransport()


def test_model_preflight_requires_exact_vision_model(mock_http):
    mock_http.get("/models", json={"data": [{"id": "deepseek-v4-flash"}]})
    with pytest.raises(ModelConfigurationError, match="MODEL_ID_NOT_FOUND"):
        DeepSeekVisionClient("runtime/runtime", transport=mock_http).verify_model()


def test_model_preflight_succeeds_when_exact_model_present(mock_http):
    mock_http.get("/models", json={"data": [{"id": "deepseek-v4-flash"}, {"id": MODEL_ID}]})
    probe = DeepSeekVisionClient("runtime/runtime", transport=mock_http).verify_model()
    assert probe.requested_model == MODEL_ID
    assert probe.observed_model == MODEL_ID
    assert MODEL_ID in probe.available_models
    assert probe.status == "ok"


def test_timeout_and_429_retry_exactly_once(mock_http):
    mock_http.queue_timeout_then_success(model=MODEL_ID)
    response = DeepSeekVisionClient("runtime/runtime", transport=mock_http).complete(
        [InputPart("text", "text/plain", "question", sha256_text("question"))],
        response_schema={"type": "object"},
        correlation_id="journey:test",
    )
    assert response.model == MODEL_ID
    assert response.http_attempts == 2
    assert response.retry_count == 1

    mock_http.queue_429_then_429()
    with pytest.raises(DeepSeekRetryExhausted):
        DeepSeekVisionClient("runtime/runtime", transport=mock_http).complete(
            [], response_schema={"type": "object"}, correlation_id="journey:test-2"
        )


def test_complete_forwards_pinned_temperature_and_seed_and_uses_json_object_mode(mock_http):
    DeepSeekVisionClient("runtime/runtime", transport=mock_http).complete(
        [InputPart("text", "text/plain", "question", sha256_text("question"))],
        response_schema={"type": "object"},
        correlation_id="journey:test-determinism",
        temperature=0.0, seed=0,
    )
    sent = json.loads(mock_http.last_request.content)
    assert sent["temperature"] == 0.0
    assert sent["seed"] == 0
    assert sent["response_format"] == {"type": "json_object"}
    assert "json" in sent["messages"][0]["content"].lower()
    assert sent["max_tokens"] > 4096  # generous headroom over reasoning tokens


def test_non_retryable_provider_error_makes_only_one_http_attempt(mock_http):
    mock_http.queue_responses(httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(DeepSeekProviderError):
        DeepSeekVisionClient("runtime/runtime", transport=mock_http).complete(
            [], response_schema={"type": "object"}, correlation_id="journey:test-3"
        )
    assert mock_http.post_attempts == 1


def test_client_has_no_endpoint_override_and_uses_official_origin():
    assert set(inspect.signature(DeepSeekVisionClient).parameters) == {"api_key", "timeout_seconds", "transport"}


def test_client_requires_non_empty_api_key(mock_http):
    with pytest.raises(ModelConfigurationError):
        DeepSeekVisionClient("", transport=mock_http)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.deepseek.com/models",
        "https://evil.example.invalid/models",
        "https://user:pass@api.deepseek.com/models",
        "https://api.deepseek.com:8443/models",
        "https://api.deepseek.com/models?redirect=evil",
    ],
)
def test_official_origin_validation_rejects_unsafe_url(url):
    with pytest.raises(DeepSeekEndpointError):
        validate_official_url(url)


def test_official_origin_validation_accepts_exact_urls():
    validate_official_url("https://api.deepseek.com/models")
    validate_official_url("https://api.deepseek.com/chat/completions")
