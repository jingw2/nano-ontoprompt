"""Snapshot-backed Sandbox simulation (Task 22).

`simulate_action` is the one place that turns an already-persisted,
immutable `RuntimePlan` (Task 15's `RuntimeService.create_action_plan`)
into a bounded, disposable preview of what would happen if it were ever
executed — without ever executing anything. It loads the plan through
`RuntimeService.get_action_plan` (which already proves the plan belongs to
the calling principal), re-confirms the plan's pinned `SemanticSnapshot`
is still governed and its pinned `Action` is still eligible, and — when the
plan carries a Task 21 managed action binding — re-resolves that binding to
confirm it is still `published` and undrifted. Only then does it compute a
field-level before/after diff and expected row impact from the plan's own
already-frozen `predicted_diff`, and persist an append-only
`SandboxSimulation` row.

This module never imports or calls a database-writer/connector
implementation — building and running a real allowlisted UPDATE against an
external connection is explicitly deferred to a later Milestone 3 task, the
same non-goal Task 21's `freeze_action_target` already established. It never
executes arbitrary SQL and never invokes prompt/tool/Agent execution: a plan
that is not bound to a currently-published managed action binding is
rejected outright as `UNSUPPORTED_ACTION`, because Sandbox v1 has no safe,
allowlisted surface to reason about what an unbound action would actually
do.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.models.action import Action
from app.models.sandbox import SandboxSimulation
from app.schemas.runtime import ReasonCode
from app.services.runtime.action_bindings import BindingError, resolve_published_binding
from app.services.runtime.credentials import RuntimeContext
from app.services.runtime.service import RuntimeService
from app.services.runtime.snapshots import SnapshotValidationError, get_snapshot


class SandboxError(Exception):
    """Structured denial for a Sandbox simulation. `reason_code` is stable
    and safe to surface to a caller — mirrors `RuntimeAccessError` (Task 13)
    and `BindingError` (Task 21)."""

    def __init__(self, reason_code: str, message: str | None = None):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


@dataclass(frozen=True)
class SandboxResult:
    """Immutable read projection of one persisted `SandboxSimulation` row."""

    simulation_id: str
    action_plan_id: str
    expected_rows: int
    before_after_diff: Mapping[str, Any]
    impact_summary: Mapping[str, Any]
    rule_outcome: tuple[Mapping[str, Any], ...]
    policy_result: Mapping[str, Any]
    precondition_hashes: Mapping[str, str]
    expires_at: datetime


def _compute_before_after_diff(predicted_diff: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """A field-level diff over exactly the columns the plan actually writes
    (`predicted_diff["after"]`'s keys) — never the full before-image, which
    may carry unrelated fields the action never touches."""
    after = predicted_diff.get("after") or {}
    before = predicted_diff.get("before")
    before_map = before if isinstance(before, Mapping) else {}
    return {key: {"before": before_map.get(key), "after": value} for key, value in after.items()}


def simulate_action(db: Session, *, plan_id: str, context: RuntimeContext) -> SandboxResult:
    """Simulate `plan_id` against its pinned snapshot and (if bound) managed
    action binding. Reads only the immutable plan, the snapshot's governed
    state, and the binding's publication state — never a production
    connector, never arbitrary SQL, never prompt/tool/Agent execution."""
    plan = RuntimeService().get_action_plan(plan_id, context, db)

    try:
        snapshot = get_snapshot(db, plan.semantic_snapshot_id)
    except SnapshotValidationError as exc:
        raise SandboxError(ReasonCode.SNAPSHOT_NOT_GOVERNED.value, str(exc)) from exc

    action = db.get(Action, plan.action_id)
    if action is None or not action.enabled:
        raise SandboxError(ReasonCode.UNSUPPORTED_ACTION.value, "action is not eligible for simulation")

    if plan.managed_action_binding_id is not None:
        try:
            binding_version = int(plan.binding_version)
        except (TypeError, ValueError) as exc:
            raise SandboxError(ReasonCode.BINDING_DRIFT.value, "binding_version is not resolvable") from exc
        try:
            resolve_published_binding(db, plan.managed_action_binding_id, binding_version)
        except BindingError as exc:
            raise SandboxError(exc.reason_code, str(exc)) from exc

    before_after_diff = _compute_before_after_diff(plan.predicted_diff)
    expected_rows = int(plan.impact_scope.get("instance_count", 0) or 0)
    impact_summary = dict(plan.impact_scope)
    rule_outcome = [outcome.model_dump(mode="json") for outcome in plan.rule_outcomes]
    policy_result = dict(plan.policy_decision)
    precondition_hashes = {
        "plan_hash": plan.plan_hash,
        "before_image_hash": plan.before_image_hash,
        "version_hash": plan.version_hash,
        "materialization_hash": snapshot.materialization_hash,
    }

    row = SandboxSimulation(
        id=str(uuid.uuid4()),
        action_plan_id=plan.id,
        semantic_snapshot_id=plan.semantic_snapshot_id,
        ontology_release_id=plan.ontology_release_id,
        agent_id=plan.agent_id,
        user_id=plan.user_id,
        managed_action_binding_id=plan.managed_action_binding_id,
        binding_version=plan.binding_version,
        expected_rows=expected_rows,
        before_after_diff=before_after_diff,
        impact_summary=impact_summary,
        rule_outcome=rule_outcome,
        policy_result=policy_result,
        # A simulation is never more durable than the plan it previews —
        # it can never outlive the plan's own governed expiry.
        expires_at=plan.expiry,
        precondition_hashes=precondition_hashes,
        status="simulated",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return SandboxResult(
        simulation_id=row.id,
        action_plan_id=plan.id,
        expected_rows=expected_rows,
        before_after_diff=MappingProxyType(before_after_diff),
        impact_summary=MappingProxyType(impact_summary),
        rule_outcome=tuple(rule_outcome),
        policy_result=MappingProxyType(policy_result),
        precondition_hashes=MappingProxyType(precondition_hashes),
        expires_at=plan.expiry,
    )


__all__ = ["SandboxError", "SandboxResult", "simulate_action"]
