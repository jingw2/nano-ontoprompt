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
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.deps.runtime import _UNAUTHENTICATED_REASONS, runtime_bearer
from app.models.oauth import OAuthClient
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.user import User
from app.schemas.runtime import InvestigationRequest, InvestigationResult, ReasonCode
from app.services.actions.approval import ApprovalReceipt
from app.services.authorization import role_allows
from app.services.runtime.action_bindings import BindingError, PlanValidationError
from app.services.runtime.credentials import (
    RuntimeAccessError,
    RuntimeContext,
    issue_delegated_credential,
    verify_delegated_credential,
)
from app.services.runtime.execution import (
    ExecutionError,
    ExecutionReceipt,
    create_rollback_plan,
    execute_plan,
    get_execution_status as _get_execution_status,
    record_plan_approval,
)
from app.services.runtime.policy import _as_aware_utc
from app.services.runtime.reconciliation import ReconciliationCase, get_reconciliation_case
from app.services.runtime.sandbox import SandboxError, SandboxResult, simulate_action
from app.services.runtime.service import ActionPlan, ActionPlanRequest, RuntimeService

router = APIRouter()

# The audience every REST-issued delegated credential must carry to be
# accepted by this transport; a credential minted for a different transport
# (e.g. MCP) with a different audience is rejected the same way an expired
# or forged one would be.
RUNTIME_REST_AUDIENCE = "ontexus-runtime"
READ_SCOPE = "ontology:read"
WRITE_SCOPE = "ontology:write"
RUNTIME_DELEGATION_TTL_SECONDS = 900

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


async def runtime_access_error_handler(
    request, exc: RuntimeAccessError | ExecutionError | BindingError | PlanValidationError | SandboxError,
) -> JSONResponse:
    """The single place every structured Runtime denial — credential-layer
    (`RuntimeAccessError`) or service-layer (`ExecutionError`/`BindingError`/
    `PlanValidationError`/`SandboxError`, Tasks 21/22/26) — becomes an HTTP
    response. Every one of these exception classes carries only a stable
    `reason_code`, so one handler (registered for each class in
    `app.main`) maps all of them identically; no route ever needs its own
    denial-mapping logic."""
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


class ApproveRequestBody(BaseModel):
    """POST /approve accepts only the plan's own exact hash — nothing else
    is ever read from the body (mirrors `ActionPlanRequestBody`'s
    `extra="forbid"` discipline)."""

    model_config = ConfigDict(extra="forbid")

    plan_hash: str


class ExecuteRequestBody(BaseModel):
    """POST /execute accepts only `{"plan_hash": string}` — never a
    caller-selected target, parameters, SQL, identifiers, connection
    target, or transaction options. `execute_plan`'s own `selector`/
    `parameters` arguments are left at their default `None`; nothing this
    body carries is ever passed to them."""

    model_config = ConfigDict(extra="forbid")

    plan_hash: str


class RollbackRequestBody(BaseModel):
    """POST /rollback-plans takes no caller-supplied fields — the execution
    to roll back is identified entirely by the path parameter, never the
    body."""

    model_config = ConfigDict(extra="forbid")


class DelegationRequestBody(BaseModel):
    """A signed-in user delegates only their own Runtime authority to one
    pre-registered agent. Persisted agent audience/scope allowlists remain
    authoritative at issuance and verification time.

    `scopes` is additionally restricted to `{READ_SCOPE, WRITE_SCOPE}` here,
    independent of whatever an agent's own `allowed_scopes` happen to
    permit — a caller can never request a scope this transport does not
    itself recognize, regardless of how a given `OAuthClient` is configured."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    scopes: set[str] = Field(min_length=1)

    @field_validator("scopes")
    @classmethod
    def _scopes_within_allowlist(cls, value: set[str]) -> set[str]:
        allowed = {READ_SCOPE, WRITE_SCOPE}
        if not value <= allowed:
            raise ValueError(f"scopes must be a subset of {sorted(allowed)}")
        return value


@router.get("/delegation-agents")
def list_delegation_agents(
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List only pre-registered Runtime-capable agent metadata. Existing
    bearer credentials remain unobservable and cannot be selected or reused."""
    return [
        {"id": client.id, "client_name": client.client_name, "allowed_scopes": list(client.allowed_scopes or [])}
        for client in db.query(OAuthClient).filter(
            OAuthClient.is_active.is_(True), OAuthClient.security_domain_id == _.security_domain_id,
        ).all()
        if RUNTIME_REST_AUDIENCE in (client.allowed_audiences or [])
    ]


def _user_has_capability_intersection(db: Session, *, user_id: str, capability_names: list[str]) -> bool:
    """Mirrors the same "Agent capability ∩ user entitlement" discipline
    `evaluate_access` (`app.services.runtime.policy`) already applies to
    every other Runtime request: a signed-in user may only delegate to an
    agent whose own registered `capability_names` genuinely overlap with an
    active `OntologyDataGrant` they hold. Delegation issuance happens
    before any specific ontology/snapshot is chosen, so — unlike
    `evaluate_access` — this checks across all of the caller's active
    grants rather than one scoped to a single `ontology_id`."""
    if not capability_names:
        return False
    now = datetime.now(timezone.utc)
    grants = db.execute(
        select(OntologyDataGrant).where(
            OntologyDataGrant.user_id == user_id,
            OntologyDataGrant.status == "active",
        )
    ).scalars().all()
    return any(
        set(grant.capabilities or []) & set(capability_names)
        and (grant.valid_from is None or _as_aware_utc(grant.valid_from) <= now)
        and (grant.valid_until is None or now < _as_aware_utc(grant.valid_until))
        for grant in grants
    )


@router.post("/delegations")
def issue_browser_delegation(
    body: DelegationRequestBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Mint a short-lived, memory-only Runtime credential for the signed-in
    user. The browser cannot assert or override either principal."""
    agent = db.query(OAuthClient).filter(
        OAuthClient.id == body.agent_id,
        OAuthClient.is_active.is_(True),
        OAuthClient.security_domain_id == current_user.security_domain_id,
    ).one_or_none()
    if agent is None:
        # Deliberately indistinguishable from a missing/inactive agent: a
        # caller cannot use issuance as a cross-domain agent oracle.
        raise RuntimeAccessError("AGENT_INACTIVE")
    if WRITE_SCOPE in body.scopes and not role_allows(current_user.role, "editor"):
        # A viewer-role session must never mint a write-capable Runtime
        # credential, mirroring the same editor ceiling
        # `app.deps.require_editor` already enforces for every other
        # mutating action in this codebase. Denied the same indistinguishable
        # way as a missing/wrong-domain agent — this check must not become a
        # second, distinguishable error code an unauthorized caller could
        # use to learn anything about the agent itself.
        raise RuntimeAccessError("AGENT_INACTIVE")
    if not _user_has_capability_intersection(db, user_id=current_user.id, capability_names=agent.capability_names or []):
        # The Critical-severity fix: without this, any authenticated user in
        # the same security domain could mint a credential against the most
        # capable registered agent regardless of whether they hold any
        # entitlement connecting them to it. Denied the same indistinguishable
        # way as a missing/wrong-domain agent (see above) — an unauthorized
        # caller cannot tell "agent doesn't exist" apart from "you have no
        # grant that intersects this agent's capabilities."
        raise RuntimeAccessError("AGENT_INACTIVE")
    token = issue_delegated_credential(
        db, client_id=body.agent_id, user_id=current_user.id,
        audience=RUNTIME_REST_AUDIENCE, scope=set(body.scopes),
        ttl_seconds=RUNTIME_DELEGATION_TTL_SECONDS, now=datetime.now(timezone.utc),
    )
    return {"token": token, "expires_in": RUNTIME_DELEGATION_TTL_SECONDS}


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


def _serialize_sandbox_result(result: SandboxResult, *, semantic_snapshot_id: str) -> dict[str, Any]:
    # `SandboxResult` (Task 22) carries no `semantic_snapshot_id` of its
    # own — it is sourced here from the plan `simulate_action` already
    # resolved, never hardcoded.
    return {
        "simulation_id": result.simulation_id,
        "action_plan_id": result.action_plan_id,
        "semantic_snapshot_id": semantic_snapshot_id,
        "expected_rows": result.expected_rows,
        "before_after_diff": dict(result.before_after_diff),
        "impact_summary": dict(result.impact_summary),
        "rule_outcome": [dict(outcome) for outcome in result.rule_outcome],
        "policy_result": dict(result.policy_result),
        "precondition_hashes": dict(result.precondition_hashes),
        "expires_at": result.expires_at.isoformat(),
    }


def _serialize_approval_receipt(receipt: ApprovalReceipt) -> dict[str, Any]:
    return {
        "plan_id": receipt.plan_id,
        "plan_hash": receipt.plan_hash,
        "approver_agent_id": receipt.approver_agent_id,
        "approver_user_id": receipt.approver_user_id,
        "approved_at": receipt.approved_at.isoformat(),
        "execution_class": receipt.execution_class,
        "expiry": receipt.expiry.isoformat(),
        "correlation_id": receipt.correlation_id,
    }


def _serialize_execution_receipt(receipt: ExecutionReceipt) -> dict[str, Any]:
    return {
        "execution_id": receipt.execution_id,
        "plan_id": receipt.plan_id,
        "plan_hash": receipt.plan_hash,
        "status": receipt.status,
        "execution_class": receipt.execution_class,
        "dialect": receipt.dialect,
        "writer_receipt": dict(receipt.writer_receipt) if receipt.writer_receipt is not None else None,
        "audit_id": receipt.audit_id,
        "idempotency_key": receipt.idempotency_key,
        "reconciliation_case_id": receipt.reconciliation_case_id,
    }


# A `RuntimeReconciliationCase.status` ("open" / "resolved_succeeded" /
# "resolved_failed", `app.services.runtime.reconciliation`) is a workflow
# state, not the underlying execution outcome it exists to describe. Every
# case is opened only for an `UNKNOWN` execution outcome (`execute_plan`
# never opens one for a definite SUCCEEDED/FAILED result), so an "open" case
# always means the outcome is still `UNKNOWN`; a human's resolution
# (`resolve_reconciliation_case`) is what ultimately determines whether the
# write actually `SUCCEEDED` or `FAILED`. The wire shape surfaces that
# outcome vocabulary directly, rather than the internal workflow-state
# vocabulary, so a caller never has to know the case/execution status split.
_RECONCILIATION_STATUS = {
    "open": "UNKNOWN",
    "resolved_succeeded": "SUCCEEDED",
    "resolved_failed": "FAILED",
}


def _serialize_reconciliation_case(case: ReconciliationCase) -> dict[str, Any]:
    return {
        "id": case.id,
        "execution_id": case.execution_id,
        "plan_id": case.plan_id,
        "status": _RECONCILIATION_STATUS.get(case.status, case.status),
        "unknown_reason": case.unknown_reason,
        "observed_effect": dict(case.observed_effect),
        "next_action": case.next_action,
        "created_at": case.created_at.isoformat(),
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
    # Task 26 built real governed execution (`app.services.runtime.execution
    # .get_execution_status`) after this endpoint's original "Phase 3
    # execution does not exist yet" placeholder was written — wired to the
    # real status here. Ownership is verified through the same
    # `get_action_plan` visibility check used by GET /action-plans/{plan_id}
    # (and internally by `get_execution_status` itself), so this stays one
    # authorization path, never a second, divergent one. `None` means no
    # execution has ever been attempted for this plan — the same stable
    # "not started" shape this endpoint always returned.
    plan = _service.get_action_plan(plan_id, context, db)
    receipt = _get_execution_status(db, plan_id=plan.id, context=context)
    if receipt is None:
        return {"plan_id": plan.id, "status": "not_started", "correlation_id": context.correlation_id}
    return {**_serialize_execution_receipt(receipt), "correlation_id": context.correlation_id}


@router.get("/action-plans/{plan_id}/sandbox")
def get_sandbox(
    plan_id: str,
    context: RuntimeContext = Depends(_require_runtime_context(READ_SCOPE)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # `simulate_action` itself re-resolves the plan (and its own ownership
    # check) internally; resolving it here first is what lets this route add
    # `semantic_snapshot_id` to the wire response — `SandboxResult` (Task 22)
    # carries no such field of its own.
    plan = _service.get_action_plan(plan_id, context, db)
    result = simulate_action(db, plan_id=plan_id, context=context)
    return _serialize_sandbox_result(result, semantic_snapshot_id=plan.semantic_snapshot_id)


@router.post("/action-plans/{plan_id}/approve")
def approve_plan(
    plan_id: str,
    body: ApproveRequestBody,
    context: RuntimeContext = Depends(_require_runtime_context(WRITE_SCOPE)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # `record_plan_approval` (Task 26) — never the bare, stateless
    # `approve_exact_plan` (Task 23) — is the only function that durably
    # persists a `RuntimeExecutionApproval` row; `execute_plan` requires that
    # durable row to exist before it will ever execute a `HUMAN_APPROVED`
    # plan. Calling `approve_exact_plan` directly here would leave nothing
    # for a later `execute_plan` call to find.
    receipt = record_plan_approval(db, plan_id=plan_id, presented_plan_hash=body.plan_hash, approver_context=context)
    return _serialize_approval_receipt(receipt)


@router.post("/action-plans/{plan_id}/execute")
def execute_plan_route(
    plan_id: str,
    body: ExecuteRequestBody,
    context: RuntimeContext = Depends(_require_runtime_context(WRITE_SCOPE)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # `selector`/`parameters` are deliberately never passed — the body
    # carries only `plan_hash`, so there is nothing to forward even if a
    # caller could smuggle an override field past `extra="forbid"`.
    receipt = execute_plan(db, plan_id=plan_id, presented_plan_hash=body.plan_hash, context=context)
    return _serialize_execution_receipt(receipt)


@router.get("/reconciliations/{reconciliation_id}")
def get_reconciliation(
    reconciliation_id: str,
    context: RuntimeContext = Depends(_require_runtime_context(READ_SCOPE)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    case = get_reconciliation_case(db, case_id=reconciliation_id)
    if case is None:
        # Existence-hiding, mirroring `RuntimeService.get_action_plan`: a
        # nonexistent case and one this caller does not own (checked next)
        # are indistinguishable.
        raise RuntimeAccessError(ReasonCode.POLICY_DENIED.value)
    # `ReconciliationCase` carries no `agent_id`/`user_id` of its own — only
    # `plan_id`. Cross-check ownership through the same existence-hiding
    # `get_action_plan` check every other Runtime endpoint relies on, so a
    # caller holding a valid credential for a DIFFERENT plan can never read
    # this case by guessing/enumerating reconciliation ids.
    _service.get_action_plan(case.plan_id, context, db)
    return _serialize_reconciliation_case(case)


@router.post("/executions/{execution_id}/rollback-plans", status_code=201)
def create_rollback_plan_route(
    execution_id: str,
    body: RollbackRequestBody,
    context: RuntimeContext = Depends(_require_runtime_context(WRITE_SCOPE)),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    plan = create_rollback_plan(db, execution_id=execution_id, context=context)
    return _serialize_action_plan(plan)


__all__ = ["router", "runtime_access_error_handler", "RuntimeAccessError"]
