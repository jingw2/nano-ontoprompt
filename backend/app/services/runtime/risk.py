"""Risk-based execution routing (Task 23).

`evaluate_execution_policy` is the one place every governed write asks "how
may this already-simulated `RuntimePlan` (Task 15) ever be executed?" before
it is ever handed to a real database writer (a later Milestone 3 task). It
is a pure decision function over an immutable `ActionPlan` (Task 15) and its
already-computed `SandboxResult` (Task 22) — never a database session,
mirroring `evaluate_access`/`evaluate_snapshot_freshness` (Task 14/20)
before it: no side effects, no re-querying live state, just a deterministic
verdict from the two artifacts' own already-frozen facts plus the caller's
verified `RuntimeContext` and `now`.

A plan/sandbox pair with anything wrong at decision time — a plan already
past its own expiry, a snapshot that had already gone stale/unknown by
plan-creation time, a caller whose identity no longer matches the plan's own
dual principal, a `SandboxResult` that does not actually belong to this
exact plan (by id or by the hashes it recorded at simulation time), or a
plan Runtime policy never actually allowed in the first place — is REJECTED
outright with a stable `ReasonCode`, never silently downgraded to a
human-approved path. Everything else is routed by risk: a plan bound to a
concrete, schema-pinned `ManagedActionBinding` (Task 21), scoped to exactly
one resolved target row, requiring no freshness grace, is the one low-risk
shape `AUTOMATIC` execution is for; anything less certain (an unbound/
free-form proposal, a multi-row or unscoped impact, or data that was already
soft-stale when the plan was created) always requires a human to approve
this plan's *exact* `plan_hash` first — see `approve_exact_plan` in
`app.services.actions.approval`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from app.schemas.runtime import ReasonCode
from app.services.runtime.credentials import RuntimeContext
from app.services.runtime.sandbox import SandboxResult
from app.services.runtime.service import ActionPlan


class ExecutionClass(str, Enum):
    AUTOMATIC = "AUTOMATIC"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class RiskDecision:
    """Immutable verdict `evaluate_execution_policy` returns.

    `risk_factors` is the human-readable "why" behind a `HUMAN_APPROVED`
    routing — always empty for `AUTOMATIC` (nothing to explain) and for
    `REJECTED` (a structural/policy failure, not a risk tradeoff, explains
    itself via `reason_code`). `required_plan_hash` is the exact hash a
    human must present back through `approve_exact_plan` — set only when
    `execution_class` is `HUMAN_APPROVED`, since an `AUTOMATIC` plan needs no
    approval and a `REJECTED` plan can never be approved no matter what hash
    is presented.
    """

    execution_class: str
    allowed: bool
    reason_code: str
    risk_factors: tuple[str, ...]
    required_plan_hash: str | None


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite (the unit-test harness) round-trips `DateTime(timezone=True)`
    values as naive; PostgreSQL preserves tzinfo. Every value this module
    ever compares against is UTC, so a naive read is always UTC too —
    mirrors the identical helper in `app.services.runtime.credentials` and
    `app.services.runtime.policy`."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _reject(reason_code: ReasonCode) -> RiskDecision:
    return RiskDecision(
        execution_class=ExecutionClass.REJECTED.value, allowed=False,
        reason_code=reason_code.value, risk_factors=(), required_plan_hash=None,
    )


def evaluate_execution_policy(
    plan: ActionPlan, sandbox: SandboxResult, context: RuntimeContext, now: datetime,
) -> RiskDecision:
    """Route an already-simulated plan to AUTOMATIC, HUMAN_APPROVED, or
    REJECTED. Checks are ordered so the first failing gate below determines
    `reason_code`: plan expiry, Sandbox/plan linkage and hash agreement,
    the plan's own captured policy allowance and dual-principal identity,
    then freshness — only a plan that clears every gate is risk-scored into
    AUTOMATIC vs HUMAN_APPROVED.
    """
    # 1. The plan itself must still be live.
    if _as_aware_utc(now) >= _as_aware_utc(plan.expiry):
        return _reject(ReasonCode.PLAN_EXPIRED)

    # 2. The presented Sandbox result must actually be a simulation of THIS
    #    exact plan — by id and by the hashes it recorded at simulation
    #    time — never a stale or mismatched preview riding along.
    if sandbox.action_plan_id != plan.id:
        return _reject(ReasonCode.PRECONDITION_CONFLICT)
    if sandbox.precondition_hashes.get("plan_hash") != plan.plan_hash:
        return _reject(ReasonCode.INVALID_PLAN_HASH)
    if (
        sandbox.precondition_hashes.get("before_image_hash") != plan.before_image_hash
        or sandbox.precondition_hashes.get("version_hash") != plan.version_hash
    ):
        return _reject(ReasonCode.PRECONDITION_CONFLICT)

    # 3. The plan's own captured Runtime policy decision must have actually
    #    allowed it, and the calling identity must still be the exact dual
    #    principal (Agent + user) it was proposed for — a re-verified
    #    credential for a *different* Agent or user is never close enough,
    #    no matter how valid that credential is on its own.
    if not plan.policy_decision.get("allowed"):
        return _reject(ReasonCode.POLICY_DENIED)
    if context.principal.agent_id != plan.agent_id or context.principal.user_id != plan.user_id:
        return _reject(ReasonCode.POLICY_DENIED)

    # 4. Freshness: `RuntimeService.create_action_plan` (Task 20) only ever
    #    persists a plan against fresh or explicitly HITL-flagged soft-stale
    #    data — any other freshness state recorded on the plan is a state
    #    this function must never trust, regardless of how it got there.
    freshness_state = plan.policy_decision.get("freshness_state")
    requires_hitl_freshness = bool(plan.policy_decision.get("requires_hitl"))
    if freshness_state == "unknown":
        return _reject(ReasonCode.SNAPSHOT_STALE)
    if freshness_state == "stale" and not requires_hitl_freshness:
        return _reject(ReasonCode.SNAPSHOT_STALE)

    # Everything below is an allowed plan; risk factors decide AUTOMATIC vs
    # HUMAN_APPROVED. Low risk == bound to a concrete schema-pinned binding,
    # scoped to exactly one resolved row, and requiring no freshness grace.
    risk_factors: list[str] = []
    if plan.managed_action_binding_id is None:
        risk_factors.append("unbound_action")
    if plan.risk_classification != "single_instance_write":
        risk_factors.append("unscoped_target")
    if sandbox.expected_rows != 1:
        risk_factors.append("multi_row_impact")
    if requires_hitl_freshness:
        risk_factors.append("freshness_soft_stale")

    if not risk_factors:
        return RiskDecision(
            execution_class=ExecutionClass.AUTOMATIC.value, allowed=True,
            reason_code=ReasonCode.ALLOW.value, risk_factors=(), required_plan_hash=None,
        )

    reason_code = (
        ReasonCode.SNAPSHOT_FRESHNESS_HITL if "freshness_soft_stale" in risk_factors else ReasonCode.ALLOW
    )
    return RiskDecision(
        execution_class=ExecutionClass.HUMAN_APPROVED.value, allowed=True,
        reason_code=reason_code.value, risk_factors=tuple(risk_factors),
        required_plan_hash=plan.plan_hash,
    )


__all__ = ["ExecutionClass", "RiskDecision", "evaluate_execution_policy"]
