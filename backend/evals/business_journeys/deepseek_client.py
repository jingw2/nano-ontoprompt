"""Strict, security-hardened HTTP client for the official DeepSeek API.

Exact model, exact origin, no endpoint override, no redirects, and a single
bounded retry for timeout/429 only. ``transport`` is a unit-test-only
injection point (``httpx.BaseTransport``); the real CI gate never supplies
one, so every real call goes over the network to ``OFFICIAL_ORIGIN``.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from typing import Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from .contracts import (
    MODEL_ID,
    OFFICIAL_ORIGIN,
    DeepSeekEndpointError,
    DeepSeekProviderError,
    DeepSeekRetryExhausted,
    InputPart,
    ModelConfigurationError,
    ModelProbe,
    ModelResponse,
)

_MODELS_PATH = "/models"
_CHAT_COMPLETIONS_PATH = "/chat/completions"
_ALLOWED_HOST = "api.deepseek.com"
_MAX_HTTP_ATTEMPTS = 2
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 2.0


def validate_official_url(url: str) -> None:
    """Reject anything but an exact, plain ``https://api.deepseek.com/...`` URL.

    Rejects non-HTTPS, wrong hostname, userinfo, a non-default port, a query
    string, and a fragment.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise DeepSeekEndpointError(f"DEEPSEEK_ENDPOINT_SCHEME_INVALID: {url}")
    try:
        has_port = parts.port is not None
    except ValueError as exc:
        # `.port` raises a raw ValueError for a non-numeric or out-of-range
        # port (e.g. ":abc" or ":99999") instead of returning None; convert
        # it to our typed failure so no caller can be surprised by it.
        raise DeepSeekEndpointError(f"DEEPSEEK_ENDPOINT_PORT_INVALID: {url}") from exc
    if parts.username or parts.password:
        raise DeepSeekEndpointError(f"DEEPSEEK_ENDPOINT_USERINFO_REJECTED: {url}")
    if parts.hostname != _ALLOWED_HOST:
        raise DeepSeekEndpointError(f"DEEPSEEK_ENDPOINT_HOST_INVALID: {url}")
    if has_port:
        raise DeepSeekEndpointError(f"DEEPSEEK_ENDPOINT_PORT_INVALID: {url}")
    if parts.query:
        raise DeepSeekEndpointError(f"DEEPSEEK_ENDPOINT_QUERY_REJECTED: {url}")
    if parts.fragment:
        raise DeepSeekEndpointError(f"DEEPSEEK_ENDPOINT_FRAGMENT_REJECTED: {url}")


def _encode_part(part: InputPart) -> Mapping[str, object]:
    if part.kind == "text":
        return {"type": "text", "text": part.content}
    if part.kind == "image":
        data = part.content if isinstance(part.content, (bytes, bytearray)) else str(part.content).encode("utf-8")
        encoded = base64.b64encode(bytes(data)).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{part.media_type};base64,{encoded}"}}
    raise ModelConfigurationError(f"INPUT_PART_KIND_INVALID: {part.kind!r}")


class DeepSeekVisionClient:
    """Transport for the official DeepSeek vision model, and nothing else.

    No endpoint/model-base constructor parameter exists: the two URLs this
    client ever builds are ``OFFICIAL_ORIGIN + "/models"`` and
    ``OFFICIAL_ORIGIN + "/chat/completions"``. The constructor's parameter
    set is asserted exactly by ``test_client_has_no_endpoint_override_and_
    uses_official_origin``, so the one bounded exponential-backoff delay
    before the single retry is not a constructor parameter -- it is the
    ``_sleep`` class attribute (delay computed by ``_backoff_delay_seconds``)
    below, which a test can override (e.g. ``monkeypatch.setattr(
    DeepSeekVisionClient, "_sleep", staticmethod(lambda seconds: None))``)
    to stay fast without changing the public signature.
    """

    _sleep = staticmethod(time.sleep)

    def __init__(self, api_key: str, *, timeout_seconds: float = 45.0, transport: httpx.BaseTransport | None = None) -> None:
        if not api_key:
            raise ModelConfigurationError("DEEPSEEK_API_KEY_REQUIRED: empty api key")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    @staticmethod
    def _backoff_delay_seconds(attempt: int) -> float:
        """Bounded exponential backoff for the delay before a retry.

        ``attempt`` is the attempt number that just failed (1 for the
        first). Only ever called once today (``_MAX_HTTP_ATTEMPTS`` is 2,
        so there is exactly one retry), but the formula is genuinely
        exponential, not a fixed sleep.
        """
        return min(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), _BACKOFF_MAX_SECONDS)

    def _client(self) -> httpx.Client:
        kwargs: dict[str, object] = {
            "timeout": self._timeout_seconds,
            "follow_redirects": False,
            "headers": {"Authorization": f"Bearer {self._api_key}"},
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    @staticmethod
    def _reject_redirect(response: httpx.Response, url: str) -> None:
        if response.has_redirect_location or 300 <= response.status_code < 400:
            raise DeepSeekEndpointError(f"DEEPSEEK_ENDPOINT_REDIRECT_REJECTED: {url}")

    def verify_model(self) -> ModelProbe:
        url = f"{OFFICIAL_ORIGIN}{_MODELS_PATH}"
        validate_official_url(url)
        with self._client() as client:
            try:
                response = client.get(url)
            except httpx.TimeoutException as exc:
                raise DeepSeekProviderError(
                    "DEEPSEEK_PROVIDER_TIMEOUT: /models request timed out", category="timeout"
                ) from exc
            except httpx.HTTPError as exc:
                raise DeepSeekProviderError(
                    "DEEPSEEK_PROVIDER_ERROR: /models request failed", category="provider_error"
                ) from exc
        self._reject_redirect(response, url)
        if response.status_code == 429:
            raise DeepSeekProviderError("DEEPSEEK_PROVIDER_ERROR: /models rate limited", category="429")
        if response.status_code != 200:
            raise DeepSeekProviderError(
                f"DEEPSEEK_PROVIDER_ERROR: /models returned {response.status_code}", category="provider_error"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise DeepSeekProviderError(
                "DEEPSEEK_PROVIDER_MALFORMED_RESPONSE: /models body was not JSON", category="provider_error"
            ) from exc
        available = tuple(
            str(item.get("id")) for item in payload.get("data", []) if isinstance(item, dict) and item.get("id")
        )
        if MODEL_ID not in available:
            raise ModelConfigurationError(f"MODEL_ID_NOT_FOUND: {MODEL_ID!r} not in {available!r}")
        return ModelProbe(requested_model=MODEL_ID, available_models=available, observed_model=MODEL_ID, status="ok")

    def complete(
        self,
        parts: Sequence[InputPart],
        *,
        response_schema: Mapping[str, object],
        correlation_id: str,
        system_instruction: str | None = None,
    ) -> ModelResponse:
        """``system_instruction``, when supplied, is sent as a leading
        ``system`` role message on the SAME completion -- it never adds a
        second HTTP request or a second logical model call, so the
        journey's ``logical_model_calls``/``max_http_attempts`` budget is
        unaffected."""
        url = f"{OFFICIAL_ORIGIN}{_CHAT_COMPLETIONS_PATH}"
        validate_official_url(url)
        messages: list[Mapping[str, object]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": [_encode_part(part) for part in parts]})
        body = {
            "model": MODEL_ID,
            "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": {"schema": dict(response_schema)}},
        }

        retry_count = 0
        with self._client() as client:
            for attempt in range(1, _MAX_HTTP_ATTEMPTS + 1):
                is_last_attempt = attempt == _MAX_HTTP_ATTEMPTS
                try:
                    response = client.post(url, json=body)
                except httpx.TimeoutException as exc:
                    if is_last_attempt:
                        raise DeepSeekRetryExhausted(
                            f"DEEPSEEK_RETRY_EXHAUSTED_TIMEOUT: {correlation_id}",
                            category="timeout",
                            http_attempts=attempt,
                        ) from exc
                    retry_count += 1
                    self._sleep(self._backoff_delay_seconds(attempt))
                    continue

                self._reject_redirect(response, url)

                if response.status_code == 429:
                    if is_last_attempt:
                        raise DeepSeekRetryExhausted(
                            f"DEEPSEEK_RETRY_EXHAUSTED_429: {correlation_id}",
                            category="429",
                            http_attempts=attempt,
                        )
                    retry_count += 1
                    self._sleep(self._backoff_delay_seconds(attempt))
                    continue

                if response.status_code != 200:
                    raise DeepSeekProviderError(
                        f"DEEPSEEK_PROVIDER_ERROR: status={response.status_code}", category="provider_error"
                    )

                return self._parse_response(response, http_attempts=attempt, retry_count=retry_count)

        raise DeepSeekProviderError("DEEPSEEK_PROVIDER_ERROR: exhausted attempts", category="provider_error")

    @staticmethod
    def _parse_response(response: httpx.Response, *, http_attempts: int, retry_count: int) -> ModelResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise DeepSeekProviderError(
                "DEEPSEEK_PROVIDER_MALFORMED_RESPONSE: body was not JSON", category="provider_error"
            ) from exc

        observed_model = payload.get("model")
        if observed_model != MODEL_ID:
            raise ModelConfigurationError(f"MODEL_ID_MISMATCH: observed {observed_model!r}")

        try:
            content = payload["choices"][0]["message"]["content"]
            structured = json.loads(content) if isinstance(content, str) else content
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DeepSeekProviderError(
                "DEEPSEEK_PROVIDER_MALFORMED_RESPONSE: missing structured content", category="provider_error"
            ) from exc

        now = datetime.now(timezone.utc)
        return ModelResponse(
            model=observed_model,
            structured=structured,
            logical_call_id=None,
            http_attempts=http_attempts,
            retry_count=retry_count,
            requested_at=now,
            completed_at=now,
        )
