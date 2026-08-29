"""Task 17: `RuntimeClient` — a typed Python SDK over the Runtime REST API
(Task 16). It is a transport adapter only: it injects the caller's
delegated credential, calls the four Runtime REST endpoints, and decodes
the server's response into the SDK's own typed models. It never evaluates
policy, never talks to a database, and never executes a write itself — a
denial always comes from the server, and this client only turns the
server's `"decision": "DENY"` body into `RuntimeDeniedError` faithfully.
"""
from __future__ import annotations

from typing import Mapping, Protocol

from .errors import RuntimeDeniedError
from .models import (
    ActionPlan,
    ActionPlanRequest,
    ExecutionStatus,
    InvestigationRequest,
    InvestigationResult,
)
from .transport import HttpTransport, HttpxTransport


class CredentialProvider(Protocol):
    def get_delegation(self) -> str:
        ...


class RuntimeClient:
    """`base_url` is only used to construct the default `HttpxTransport`
    when no `transport` is supplied; a caller-supplied transport (e.g. a
    test double) owns its own routing."""

    def __init__(
        self,
        base_url: str,
        credential_provider: CredentialProvider,
        transport: HttpTransport | None = None,
    ) -> None:
        self._credential_provider = credential_provider
        self._transport: HttpTransport = transport if transport is not None else HttpxTransport(base_url)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._credential_provider.get_delegation()}"}

    def _call(self, method: str, path: str, json: Mapping[str, object] | None = None) -> Mapping[str, object]:
        response = self._transport.request(method, path, json, self._headers())
        if response.get("decision") == "DENY":
            raise RuntimeDeniedError(
                decision=response.get("decision"),
                reason_code=response.get("reason_code"),
                correlation_id=response.get("correlation_id"),
                semantic_snapshot_id=response.get("semantic_snapshot_id"),
            )
        return response

    def investigate(self, request: InvestigationRequest) -> InvestigationResult:
        body = request.model_dump(mode="json")
        response = self._call("POST", "/api/v2/runtime/investigate", body)
        return InvestigationResult.model_validate(response)

    def create_action_plan(self, request: ActionPlanRequest) -> ActionPlan:
        body = request.model_dump(mode="json", exclude_none=True)
        response = self._call("POST", "/api/v2/runtime/action-plans", body)
        return ActionPlan.model_validate(response)

    def get_action_plan(self, plan_id: str) -> ActionPlan:
        response = self._call("GET", f"/api/v2/runtime/action-plans/{plan_id}")
        return ActionPlan.model_validate(response)

    def get_execution_status(self, plan_id: str) -> ExecutionStatus:
        response = self._call("GET", f"/api/v2/runtime/execution-status/{plan_id}")
        return ExecutionStatus.model_validate(response)


__all__ = ["RuntimeClient", "CredentialProvider"]
