"""Task 16: expose the shared Runtime (Task 15's `RuntimeService`) through
versioned REST endpoints.

This router is a thin transport adapter — it verifies the delegated
credential (Task 13), builds request models from the request body (never
from caller-asserted identity fields), calls `RuntimeService`, and maps the
result/error onto HTTP. No policy or query behavior is duplicated here; that
all lives in `RuntimeService` / `evaluate_access`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.deps import get_db
from app.deps.runtime import _UNAUTHENTICATED_REASONS, runtime_bearer
from app.schemas.runtime import InvestigationRequest, InvestigationResult
from app.services.runtime.credentials import (
    RuntimeAccessError,
    RuntimeContext,
    verify_delegated_credential,
)
from app.services.runtime.service import ActionPlan, ActionPlanRequest, RuntimeService

router = APIRouter()

# The audience every REST-issued delegated credential must carry to be
# accepted by this transport; a credential minted for a different transport
# (e.g. MCP) with a different audience is rejected the same way an expired
# or forged one would be.
RUNTIME_REST_AUDIENCE = "ontexus-runtime"
READ_SCOPE = "ontology:read"
WRITE_SCOPE = "ontology:write"

_service = RuntimeService()


def _status_for(reason_code: str) -> int:
    return 401 if reason_code in _UNAUTHENTICATED_REASONS else 403


def _denial_body(reason_code: str) -> dict[str, Any]:
    # A `RuntimeAccessError` carries only a reason code — it is raised either
    # before a `RuntimeContext` exists (credential verification) or before a
    # snapshot/plan is resolved (`RuntimeService`), so no correlation id or
    # snapshot identifiers are ever available to attach here. A denial with
    # richer context is returned as a normal `InvestigationResult` payload by
    # the `investigate` route instead of raising.
    return {
        "decision": "DENY",
        "reason_code": reason_code,
        "correlation_id": None,
        "semantic_snapshot_id": None,
        "ontology_release_id": None,
        "result": None,
    }


def _require_runtime_context(required_scope: str):
    """A dependency requiring a verified delegated credential (Task 13) for
    `RUNTIME_REST_AUDIENCE` and `required_scope`. Reuses the same bearer
    extractor and verification function `app.deps.runtime.get_runtime_context`
    wraps, but raises the domain `RuntimeAccessError` instead of an
    `HTTPException` so every denial — whether from credential verification or
    from `RuntimeService` itself — is mapped to the wire exactly once, by
    `runtime_access_error_handler` below."""

    def _dependency(
        credentials: HTTPAuthorizationCredentials = Depends(runtime_bearer),
        db: Session = Depends(get_db),
    ) -> RuntimeContext:
        token = credentials.credentials if credentials else None
        return verify_delegated_credential(
            db, token, audience=RUNTIME_REST_AUDIENCE, required_scope=required_scope,
            now=datetime.now(timezone.utc),
        )

    return _dependency


async def runtime_access_error_handler(request, exc: RuntimeAccessError) -> JSONResponse:
    """The single place every `RuntimeAccessError` — credential-layer or
    service-layer — becomes an HTTP response. `RuntimeAccessError` is only
    ever raised by `app.services.runtime.*`, so registering this app-wide is
    safe: no other route can trigger it."""
    return JSONResponse(status_code=_status_for(exc.reason_code), content=_denial_body(exc.reason_code))


class ActionPlanRequestBody(BaseModel):
    """The REST wire shape for an action-plan proposal. Extra fields
    (including any caller-asserted `agent_id`/`user_id`) are rejected —
    identity is always the verified delegated credential, never the body."""

    model_config = ConfigDict(extra="forbid")

    semantic_snapshot_id: str
    action_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    target_selector: dict[str, Any] | None = None
    idempotency_key: str | None = None


def _serialize_action_plan(plan: ActionPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "semantic_snapshot_id": plan.semantic_snapshot_id,
        "ontology_release_id": plan.ontology_release_id,
        "agent_id": plan.agent_id,
        "user_id": plan.user_id,
        "action_id": plan.action_id,
        "input_facts": dict(plan.input_facts),
        "evidence_citations": [c.model_dump(mode="json") for c in plan.evidence_citations],
        "rule_outcomes": [r.model_dump(mode="json") for r in plan.rule_outcomes],
        "managed_action_binding_id": plan.managed_action_binding_id,
        "binding_version": plan.binding_version,
        "parameters": dict(plan.parameters),
        "target_key": list(plan.target_key),
        "before_image_hash": plan.before_image_hash,
        "version_hash": plan.version_hash,
        "predicted_diff": dict(plan.predicted_diff),
        "impact_scope": dict(plan.impact_scope),
        "risk_classification": plan.risk_classification,
        "policy_decision": dict(plan.policy_decision),
        "precondition_hashes": list(plan.precondition_hashes),
        "expiry": plan.expiry.isoformat(),
        "idempotency_key": plan.idempotency_key,
        "plan_hash": plan.plan_hash,
    }


@router.post("/investigate")
def investigate(
    body: InvestigationRequest,
    context: RuntimeContext = Depends(_require_runtime_context(READ_SCOPE)),
    db: Session = Depends(get_db),
) -> JSONResponse:
    result: InvestigationResult = _service.investigate(body, context, db)
    status_code = 200 if result.decision == "ALLOW" else 403
    return JSONResponse(status_code=status_code, content=jsonable_encoder(result))


@router.post("/action-plans", status_code=201)
def create_action_plan(
    body: ActionPlanRequestBody,
    context: RuntimeContext = Depends(_require_runtime_context(WRITE_SCOPE)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    service_request_kwargs: dict[str, Any] = dict(
        semantic_snapshot_id=body.semantic_snapshot_id,
        action_id=body.action_id,
        parameters=body.parameters,
        target_selector=body.target_selector,
    )
    if body.idempotency_key is not None:
        service_request_kwargs["idempotency_key"] = body.idempotency_key
    plan = _service.create_action_plan(ActionPlanRequest(**service_request_kwargs), context, db)
    return _serialize_action_plan(plan)


@router.get("/action-plans/{plan_id}")
def get_action_plan(
    plan_id: str,
    context: RuntimeContext = Depends(_require_runtime_context(READ_SCOPE)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    plan = _service.get_action_plan(plan_id, context, db)
    return _serialize_action_plan(plan)


@router.get("/execution-status/{plan_id}")
def get_execution_status(
    plan_id: str,
    context: RuntimeContext = Depends(_require_runtime_context(READ_SCOPE)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # Phase 3 execution does not exist yet — a resolvable, principal-owned
    # plan always reports the same stable "not started" status. Ownership is
    # verified through the same `get_action_plan` visibility check used by
    # GET /action-plans/{plan_id}, so this never becomes a second, divergent
    # authorization path.
    plan = _service.get_action_plan(plan_id, context, db)
    return {"plan_id": plan.id, "status": "not_started", "correlation_id": context.correlation_id}


__all__ = ["router", "runtime_access_error_handler", "RuntimeAccessError"]
