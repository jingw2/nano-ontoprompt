"""The shared Runtime service (Task 15): snapshot-pinned investigation and
immutable action-plan proposals.

`RuntimeService` is the one place every Runtime transport calls for the two
non-writing operations a delegated credential (Task 13's `RuntimeContext`)
may perform: read a governed snapshot (`investigate`) and propose a change
against it (`create_action_plan`). Both operations resolve a materialized
`SemanticSnapshot` (Task 11/12) to its governing release, run it through the
shared intersection-policy evaluator (Task 14's `evaluate_access`), and
never touch a production connector — `create_action_plan` computes and
persists a plan describing what *would* happen, never executes anything.

A request that cannot even resolve to a real materialized snapshot (for
example, a caller asserting a release id where a snapshot id belongs) fails
before either operation can build a well-formed result — `investigate`
requires a real `ontology_release_id` to pin, and `create_action_plan` has
nothing to pin a proposal to — so both raise `RuntimeAccessError` with
reason code `SNAPSHOT_NOT_GOVERNED` rather than returning a denial shape
that would otherwise have to leave required fields empty.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.action import Action
from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.ontology_release import OntologyRelease
from app.models.runtime_plan import RuntimePlan
from app.schemas.runtime import (
    EvidenceCitation,
    InvestigationRequest,
    InvestigationResult,
    ReasonCode,
    RuleOutcome,
)
from app.schemas.runtime_snapshot import SnapshotView
from app.services.runtime.credentials import RuntimeAccessError, RuntimeContext
from app.services.runtime.policy import evaluate_access
from app.services.runtime.snapshots import SnapshotValidationError, get_snapshot

INVESTIGATE_CAPABILITY = "investigate"
CREATE_PLAN_CAPABILITY = "propose_action"
ACTION_PLAN_TTL_SECONDS = 900


@dataclass(frozen=True)
class ActionPlanRequest:
    """A snapshot-pinned proposal request. Carries no authority of its own —
    the caller's identity is always the verified `RuntimeContext`, never a
    field on this request (mirrors `InvestigationRequest`)."""

    semantic_snapshot_id: str
    action_id: str
    parameters: Mapping[str, Any]
    target_selector: Mapping[str, Any] | None = None
    idempotency_key: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class ActionPlan:
    """Immutable read projection of one persisted `RuntimePlan` row."""

    id: str
    semantic_snapshot_id: str
    ontology_release_id: str
    agent_id: str
    user_id: str
    action_id: str
    input_facts: Mapping[str, Any]
    evidence_citations: tuple[EvidenceCitation, ...]
    rule_outcomes: tuple[RuleOutcome, ...]
    managed_action_binding_id: str | None
    binding_version: str | None
    parameters: Mapping[str, Any]
    target_key: tuple[Any, ...]
    before_image_hash: str
    version_hash: str
    predicted_diff: Mapping[str, Any]
    impact_scope: Mapping[str, Any]
    risk_classification: str
    policy_decision: Mapping[str, Any]
    precondition_hashes: tuple[str, ...]
    expiry: datetime
    idempotency_key: str
    plan_hash: str


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _reason_code_value(reason_code: ReasonCode | str) -> str:
    return reason_code.value if isinstance(reason_code, ReasonCode) else reason_code


def _normalize_target_key(target_selector: Mapping[str, Any] | None) -> tuple[Any, ...]:
    """A stable, order-independent identifier for the proposal's target —
    an empty selector normalizes to a fixed sentinel so an unscoped proposal
    is never confused with an unset field."""
    if not target_selector:
        return ("unscoped",)
    return tuple(sorted((str(key), str(value)) for key, value in target_selector.items()))


def _resolve_target_instance(
    db: Session, *, ontology_id: str, target_selector: Mapping[str, Any] | None,
) -> dict | None:
    """Resolve `target_selector` to at most one live `EntityInstance`, using
    only portable SQLAlchemy Core selects (no PostgreSQL-only JSON
    operators) so the lookup behaves identically on SQLite and PostgreSQL."""
    if not target_selector:
        return None
    instance_id = target_selector.get("instance_id")
    entity_type = target_selector.get("entity_type")
    stmt = select(EntityInstance).where(
        EntityInstance.ontology_id == ontology_id,
        EntityInstance.deleted_at.is_(None),
    )
    if instance_id:
        stmt = stmt.where(EntityInstance.id == instance_id)
    if entity_type:
        entity_ids = db.execute(
            select(Entity.id).where(Entity.ontology_id == ontology_id, Entity.name_en == entity_type)
        ).scalars().all()
        if not entity_ids:
            return None
        stmt = stmt.where(EntityInstance.entity_id.in_(entity_ids))
    row = db.execute(stmt.limit(1)).scalars().first()
    if row is None:
        return None
    return {"instance_id": row.id, "entity_id": row.entity_id, "revision": row.revision, "row_data": row.row_data}


def _query_snapshot_scoped_instances(
    db: Session, *, ontology_id: str, entity_type: str | None, query: str | None, limit: int,
) -> list[dict]:
    """Bounded, portable read of current instances for `ontology_id`
    (optionally filtered by entity type and a case-insensitive substring
    match against the row). Deliberately avoids the PostgreSQL-only JSONB
    containment check in `app.services.ontology_query.query_instances` so
    the same code path is exercised on both the SQLite unit harness and
    production PostgreSQL."""
    if limit <= 0:
        return []
    stmt = select(EntityInstance).where(
        EntityInstance.ontology_id == ontology_id,
        EntityInstance.deleted_at.is_(None),
    )
    if entity_type:
        entity_ids = db.execute(
            select(Entity.id).where(Entity.ontology_id == ontology_id, Entity.name_en == entity_type)
        ).scalars().all()
        if not entity_ids:
            return []
        stmt = stmt.where(EntityInstance.entity_id.in_(entity_ids))
    stmt = stmt.order_by(EntityInstance.updated_at.desc())
    results: list[dict] = []
    for row in db.execute(stmt).scalars():
        if query and query.lower() not in json.dumps(row.row_data, ensure_ascii=False).lower():
            continue
        results.append({"instance_id": row.id, "entity_id": row.entity_id, "revision": row.revision, "row_data": row.row_data})
        if len(results) >= limit:
            break
    return results


def _resolve_snapshot(db: Session, semantic_snapshot_id: str) -> SnapshotView:
    """A materialized snapshot is the only thing either Runtime operation
    ever pins to. If it cannot be resolved, neither `InvestigationResult`
    nor `ActionPlan` has a real `ontology_release_id` to carry, so this
    fails closed as a credential-layer denial rather than a data result."""
    try:
        return get_snapshot(db, semantic_snapshot_id)
    except SnapshotValidationError as exc:
        raise RuntimeAccessError(ReasonCode.SNAPSHOT_NOT_GOVERNED.value, str(exc)) from exc


class RuntimeService:
    """Stateless facade over snapshot-pinned investigation and action-plan
    proposal. Every method takes the caller's already-verified
    `RuntimeContext` and a `Session`; nothing here issues or verifies
    credentials itself (that is Task 13's `credentials` module)."""

    def investigate(self, request: InvestigationRequest, context: RuntimeContext, db: Session) -> InvestigationResult:
        snapshot = _resolve_snapshot(db, request.semantic_snapshot_id)

        decision = evaluate_access(
            context, required_capability=INVESTIGATE_CAPABILITY, snapshot=snapshot,
            ontology_id=request.ontology_id, db=db,
        )
        if not decision.allowed:
            return InvestigationResult(
                decision="DENY", reason_code=decision.reason_code,
                semantic_snapshot_id=snapshot.id, ontology_release_id=snapshot.ontology_release_id,
                evidence_citations=[], rule_outcome=[], result=None,
                correlation_id=context.correlation_id,
            )

        citations = [EvidenceCitation(**citation) for citation in snapshot.evidence_summary.get("citations", [])]
        rule_outcome = [RuleOutcome(rule_id="snapshot_governance", result="pass", reason_code=ReasonCode.ALLOW)]
        result = _query_snapshot_scoped_instances(
            db, ontology_id=request.ontology_id, entity_type=request.entity_type,
            query=request.query, limit=request.limit,
        )
        return InvestigationResult(
            decision="ALLOW", reason_code=ReasonCode.ALLOW,
            semantic_snapshot_id=snapshot.id, ontology_release_id=snapshot.ontology_release_id,
            evidence_citations=citations, rule_outcome=rule_outcome, result=result,
            correlation_id=context.correlation_id,
        )

    def create_action_plan(self, request: ActionPlanRequest, context: RuntimeContext, db: Session) -> ActionPlan:
        snapshot = _resolve_snapshot(db, request.semantic_snapshot_id)
        release = db.execute(
            select(OntologyRelease).where(OntologyRelease.id == snapshot.ontology_release_id)
        ).scalar_one_or_none()
        if release is None:
            raise RuntimeAccessError(ReasonCode.SNAPSHOT_NOT_GOVERNED.value)

        decision = evaluate_access(
            context, required_capability=CREATE_PLAN_CAPABILITY, snapshot=snapshot,
            ontology_id=release.ontology_id, db=db,
        )
        if not decision.allowed:
            raise RuntimeAccessError(_reason_code_value(decision.reason_code))

        action = db.execute(select(Action).where(Action.id == request.action_id)).scalar_one_or_none()
        if action is None or action.ontology_id != release.ontology_id or not action.enabled:
            raise RuntimeAccessError(ReasonCode.ACTION_NOT_ELIGIBLE.value)

        target_key = _normalize_target_key(request.target_selector)
        target_row = _resolve_target_instance(
            db, ontology_id=release.ontology_id, target_selector=request.target_selector,
        )
        before_image_hash = _canonical_hash(target_row)
        parameters = dict(request.parameters)

        citations = [EvidenceCitation(**citation) for citation in snapshot.evidence_summary.get("citations", [])]
        rule_outcomes = [RuleOutcome(rule_id="action_eligibility", result="pass", reason_code=ReasonCode.ALLOW)]

        version_hash = _canonical_hash({
            "semantic_snapshot_id": snapshot.id,
            "ontology_release_id": release.id,
            "action_id": action.id,
            "before_image_hash": before_image_hash,
        })
        precondition_hashes = (
            snapshot.materialization_hash,
            hashlib.sha256(release.schema_hash).hexdigest(),
            before_image_hash,
        )
        predicted_diff = {
            "target_key": list(target_key),
            "before": target_row["row_data"] if target_row else None,
            "after": dict(parameters),
        }
        impact_scope = {
            "ontology_id": release.ontology_id,
            "action_id": action.id,
            "instance_count": 1 if target_row else 0,
        }
        risk_classification = "single_instance_write" if target_row else "unscoped_proposal"
        policy_decision = {
            "allowed": decision.allowed,
            "reason_code": _reason_code_value(decision.reason_code),
            "agent_capability": decision.agent_capability,
            "user_entitlement": decision.user_entitlement,
        }
        input_facts = {"quality_summary": dict(snapshot.quality_summary)}

        now = datetime.now(timezone.utc)
        expiry = now + timedelta(seconds=ACTION_PLAN_TTL_SECONDS)
        plan_hash = _canonical_hash({
            "semantic_snapshot_id": snapshot.id,
            "ontology_release_id": release.id,
            "agent_id": context.principal.agent_id,
            "user_id": context.principal.user_id,
            "action_id": action.id,
            "parameters": parameters,
            "target_key": list(target_key),
            "before_image_hash": before_image_hash,
            "version_hash": version_hash,
            "precondition_hashes": list(precondition_hashes),
            "idempotency_key": request.idempotency_key,
        })

        row = RuntimePlan(
            id=str(uuid.uuid4()),
            semantic_snapshot_id=snapshot.id,
            ontology_release_id=release.id,
            agent_id=context.principal.agent_id,
            user_id=context.principal.user_id,
            action_id=action.id,
            input_facts=input_facts,
            evidence_citations=[citation.model_dump(mode="json") for citation in citations],
            rule_outcomes=[outcome.model_dump(mode="json") for outcome in rule_outcomes],
            managed_action_binding_id=None,
            binding_version=None,
            parameters=parameters,
            target_key=list(target_key),
            before_image_hash=before_image_hash,
            version_hash=version_hash,
            predicted_diff=predicted_diff,
            impact_scope=impact_scope,
            risk_classification=risk_classification,
            policy_decision=policy_decision,
            precondition_hashes=list(precondition_hashes),
            expiry=expiry,
            idempotency_key=request.idempotency_key,
            plan_hash=plan_hash,
            created_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_action_plan(row)

    def get_action_plan(self, plan_id: str, context: RuntimeContext, db: Session) -> ActionPlan:
        row = db.execute(select(RuntimePlan).where(RuntimePlan.id == plan_id)).scalar_one_or_none()
        if (
            row is None
            or row.agent_id != context.principal.agent_id
            or row.user_id != context.principal.user_id
        ):
            # A plan that exists for a different principal is indistinguishable
            # from one that does not exist at all — never let plan visibility
            # become an oracle for plan-id enumeration.
            raise RuntimeAccessError(ReasonCode.POLICY_DENIED.value)
        return _to_action_plan(row)


def _to_action_plan(row: RuntimePlan) -> ActionPlan:
    return ActionPlan(
        id=row.id,
        semantic_snapshot_id=row.semantic_snapshot_id,
        ontology_release_id=row.ontology_release_id,
        agent_id=row.agent_id,
        user_id=row.user_id,
        action_id=row.action_id,
        input_facts=MappingProxyType(dict(row.input_facts)),
        evidence_citations=tuple(EvidenceCitation(**c) for c in row.evidence_citations),
        rule_outcomes=tuple(RuleOutcome(**r) for r in row.rule_outcomes),
        managed_action_binding_id=row.managed_action_binding_id,
        binding_version=row.binding_version,
        parameters=MappingProxyType(dict(row.parameters)),
        target_key=tuple(tuple(item) if isinstance(item, list) else item for item in row.target_key),
        before_image_hash=row.before_image_hash,
        version_hash=row.version_hash,
        predicted_diff=MappingProxyType(dict(row.predicted_diff)),
        impact_scope=MappingProxyType(dict(row.impact_scope)),
        risk_classification=row.risk_classification,
        policy_decision=MappingProxyType(dict(row.policy_decision)),
        precondition_hashes=tuple(row.precondition_hashes),
        expiry=row.expiry,
        idempotency_key=row.idempotency_key,
        plan_hash=row.plan_hash,
    )


__all__ = [
    "ActionPlan",
    "ActionPlanRequest",
    "RuntimeService",
]
