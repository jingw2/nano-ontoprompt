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
from ontexus_runtime.models import ActionPlanRequest, InvestigationRequest


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


def action_plan_response(**overrides) -> dict:
    body = {
        "id": "plan-001",
        "semantic_snapshot_id": "snap-valid-001",
        "ontology_release_id": "release-valid-001",
        "agent_id": "agent-001",
        "user_id": "user-001",
        "action_id": "action-001",
        "input_facts": {},
        "evidence_citations": [],
        "rule_outcomes": [],
        "managed_action_binding_id": None,
        "binding_version": None,
        "parameters": {"status": "confirmed"},
        "target_key": ["unscoped"],
        "before_image_hash": "b" * 64,
        "version_hash": "c" * 64,
        "predicted_diff": {},
        "impact_scope": {},
        "risk_classification": "unscoped_proposal",
        "policy_decision": {"allowed": True, "reason_code": "ALLOW"},
        "precondition_hashes": ["d" * 64, "e" * 64],
        "expiry": "2026-01-01T00:15:00+00:00",
        "idempotency_key": "idem-001",
        "plan_hash": "f" * 64,
    }
    body.update(overrides)
    return body


def test_sdk_decodes_action_plan_semantic_fields_verbatim():
    """Task 19: `compute_plan_hash` (`app.services.runtime.canonical`) hashes
    an `ActionPlan`'s semantic fields — parameters, the normalized target,
    before-image/version hashes, preconditions, expiry, and the idempotency
    key — straight off the decoded SDK model. If the SDK model silently
    dropped, renamed, or reordered any of those, REST and the SDK could
    never hash byte-identically no matter what the canonicalizer does. This
    asserts the decoded model reproduces every one of those fields exactly
    as the server sent them."""
    response = action_plan_response()
    transport = RecordingTransport(response)
    client = RuntimeClient("https://runtime.test", StaticCredential("token-001"), transport)

    plan = client.create_action_plan(ActionPlanRequest(
        semantic_snapshot_id="snap-valid-001", action_id="action-001", parameters={"status": "confirmed"},
    ))

    assert plan.parameters == response["parameters"]
    assert plan.target_key == response["target_key"]
    assert plan.before_image_hash == response["before_image_hash"]
    assert plan.version_hash == response["version_hash"]
    assert plan.precondition_hashes == response["precondition_hashes"]
    assert plan.expiry == response["expiry"]
    assert plan.idempotency_key == response["idempotency_key"]
    # The generated storage id is decoded too, but is not a semantic field —
    # two plans that differ only in `id` still decode the same everywhere
    # else, exactly what `canonical_plan_fields` relies on to exclude it.
    transport._response = action_plan_response(id="plan-002")
    other = client.create_action_plan(ActionPlanRequest(
        semantic_snapshot_id="snap-valid-001", action_id="action-001", parameters={"status": "confirmed"},
    ))
    assert other.id != plan.id
    assert other.model_dump(mode="json", exclude={"id"}) == plan.model_dump(mode="json", exclude={"id"})
