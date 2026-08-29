"""Task 17: the Python SDK is a thin transport adapter over the Runtime
REST API (Task 16) — it must inject the delegated credential on every
request and decode the server's decision/error fields faithfully, never
re-evaluate policy or perform a database write locally.

`RecordingTransport` is a fake `HttpTransport`: it returns a canned,
already-decoded response body (mirroring what the real HTTP transport would
hand back after parsing JSON) and records the headers it was called with,
so these tests can assert on delegation injection without a real server.
"""
from __future__ import annotations

from typing import Mapping

import pytest

from ontexus_runtime.client import RuntimeClient
from ontexus_runtime.errors import RuntimeDeniedError
from ontexus_runtime.models import InvestigationRequest


class StaticCredential:
    """The simplest `CredentialProvider`: always returns the same delegated
    credential. Real callers supply their own provider (e.g. one that
    refreshes a short-lived token); the SDK never mints or verifies
    credentials itself."""

    def __init__(self, token: str) -> None:
        self._token = token

    def get_delegation(self) -> str:
        return self._token


class RecordingTransport:
    """A fake `HttpTransport` that always returns the same canned response
    body and records the last call's method/path/json/headers."""

    def __init__(self, response: Mapping[str, object]) -> None:
        self._response = response
        self.last_method: str | None = None
        self.last_path: str | None = None
        self.last_json: Mapping[str, object] | None = None
        self.last_headers: Mapping[str, str] | None = None

    def request(
        self, method: str, path: str, json: Mapping[str, object] | None, headers: Mapping[str, str]
    ) -> Mapping[str, object]:
        self.last_method = method
        self.last_path = path
        self.last_json = json
        self.last_headers = headers
        return self._response


def valid_request() -> InvestigationRequest:
    return InvestigationRequest(
        semantic_snapshot_id="snap-valid-001",
        ontology_id="ontology-supply-001",
        query="supplier SUP001",
        entity_type="Supplier",
        filters={},
        limit=20,
    )


def test_sdk_injects_delegation_and_decodes_investigation():
    transport = RecordingTransport({"decision": "ALLOW", "reason_code": "ALLOW",
        "semantic_snapshot_id": "snap-valid-001",
        "ontology_release_id": "release-valid-001",
        "evidence_citations": [], "rule_outcome": [],
        "result": [], "correlation_id": "corr-001"})
    client = RuntimeClient("https://runtime.test", StaticCredential("token-001"), transport)
    result = client.investigate(valid_request())
    assert result.decision == "ALLOW"
    assert transport.last_headers["Authorization"] == "Bearer token-001"


def test_sdk_preserves_structured_denial():
    client = RuntimeClient("https://runtime.test", StaticCredential("token-001"),
                           RecordingTransport({"decision": "DENY", "reason_code": "SCOPE_DENIED",
                                               "semantic_snapshot_id": "snap-valid-001",
                                               "correlation_id": "corr-002"}))
    with pytest.raises(RuntimeDeniedError) as exc:
        client.investigate(valid_request())
    assert exc.value.reason_code == "SCOPE_DENIED"
