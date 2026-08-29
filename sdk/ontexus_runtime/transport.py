"""Task 17: the one HTTP transport `RuntimeClient` uses by default.

`HttpTransport` is a narrow `Protocol` — anything with a matching
`request(...)` method works, real or fake (see `sdk/tests/test_runtime_client.py`'s
`RecordingTransport`). `HttpxTransport` is the only concrete implementation
the SDK ships: it sends the request and decodes the JSON body, nothing
more. It never inspects `decision`/`reason_code` itself — `RuntimeClient`
owns that.
"""
from __future__ import annotations

from typing import Mapping, Protocol

import httpx


class HttpTransport(Protocol):
    def request(
        self, method: str, path: str, json: Mapping[str, object] | None, headers: Mapping[str, str]
    ) -> Mapping[str, object]:
        ...


class HttpxTransport:
    """A thin wrapper over `httpx.Client`. Any non-2xx response still
    carries a JSON body with `decision`/`reason_code` (Task 16's denial
    mapping), so this always decodes and returns the body rather than
    raising on status — `RuntimeClient` is the single place that turns a
    `"decision": "DENY"` body into `RuntimeDeniedError`."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def request(
        self, method: str, path: str, json: Mapping[str, object] | None, headers: Mapping[str, str]
    ) -> Mapping[str, object]:
        response = self._client.request(method, path, json=json, headers=dict(headers))
        return response.json()


__all__ = ["HttpTransport", "HttpxTransport"]
