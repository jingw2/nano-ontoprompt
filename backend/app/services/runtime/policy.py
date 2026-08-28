"""Shared intersection-policy evaluator (Task 14).

`evaluate_access` is the single place every Runtime transport calls to
decide whether a verified delegated principal (Task 13's `RuntimeContext`)
may use `required_capability` against a governed semantic snapshot: the
Agent's own registered capability set (`OAuthClient.capability_names`)
intersected with the delegated user's Runtime data entitlement
(`OntologyDataGrant.capabilities`, scoped to `ontology_id` and its validity
window) intersected with runtime policy: the queried ontology must share the
verified principal's own security domain (a delegated credential only
proves the Agent and user share one domain, not that a specific ontology_id
does too), and both principals must still be live-active — a credential can
still be unexpired after the Agent or user it names has since been
deactivated.

This never inspects or returns query results — a `PolicyDecision` carries
only capability/entitlement booleans and non-protected evidence (grant and
release identifiers), so it cannot be the source of a data leak regardless
of the caller's transport. It also never takes a query result as input, so
it structurally cannot mistake "no rows matched" for a denial; that
guarantee is enforced separately, at the wire layer, by
`InvestigationResult` (see `app.schemas.runtime`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.oauth import OAuthClient
from app.models.ontology import OntologyProject
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.ontology_release import OntologyRelease
from app.models.user import User
from app.schemas.runtime import ReasonCode
from app.schemas.runtime_snapshot import SnapshotView
from app.services.runtime.credentials import RuntimeContext


@dataclass(frozen=True)
class PolicyDecision:
    """Immutable domain value returned by `evaluate_access` — never
    serialized directly over the wire (transports project it onto
    `InvestigationResult`/`ActionPlan` fields instead)."""

    allowed: bool
    reason_code: ReasonCode
    agent_capability: bool
    user_entitlement: bool
    policy_evidence: dict[str, Any] = field(default_factory=dict)


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite round-trips naive datetimes; every value this module compares
    against is UTC, so a naive read is always UTC too."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _deny(reason_code: ReasonCode, *, agent_capability: bool = False,
          user_entitlement: bool = False, **evidence: Any) -> PolicyDecision:
    return PolicyDecision(
        allowed=False, reason_code=reason_code,
        agent_capability=agent_capability, user_entitlement=user_entitlement,
        policy_evidence=evidence,
    )


def evaluate_access(
    context: RuntimeContext, *, required_capability: str, snapshot: SnapshotView,
    ontology_id: str, db: Session,
) -> PolicyDecision:
    """Compute Agent capability ∩ user entitlement ∩ runtime policy.

    Validates the snapshot's governing release and ontology identity before
    any capability/entitlement lookup, then evaluates checks in a fixed
    order so the first failing layer determines `reason_code`: snapshot
    governance, snapshot freshness, runtime policy (live active-status
    re-check), Agent capability, user entitlement.
    """
    release = db.execute(
        select(OntologyRelease).where(OntologyRelease.id == snapshot.ontology_release_id)
    ).scalar_one_or_none()
    if release is None or release.ontology_id != ontology_id:
        return _deny(ReasonCode.SNAPSHOT_NOT_GOVERNED, ontology_release_id=snapshot.ontology_release_id)

    project = db.execute(
        select(OntologyProject).where(OntologyProject.id == release.ontology_id)
    ).scalar_one_or_none()
    if project is None or release.status != "published" or project.latest_published_release_id != release.id:
        return _deny(ReasonCode.SNAPSHOT_STALE, ontology_release_id=release.id)

    if project.security_domain_id != context.principal.security_domain_id:
        return _deny(ReasonCode.CROSS_SECURITY_DOMAIN, ontology_id=project.id)

    client = db.execute(
        select(OAuthClient).where(OAuthClient.id == context.principal.agent_id)
    ).scalar_one_or_none()
    user = db.execute(
        select(User).where(User.id == context.principal.user_id)
    ).scalar_one_or_none()
    if client is None or user is None or not client.is_active or not user.is_active:
        return _deny(ReasonCode.POLICY_DENIED)

    agent_capability = required_capability in (client.capability_names or [])
    if not agent_capability:
        return _deny(ReasonCode.AGENT_CAPABILITY_DENIED, agent_id=client.id)

    now = datetime.now(timezone.utc)
    grants = db.execute(
        select(OntologyDataGrant).where(
            OntologyDataGrant.ontology_id == ontology_id,
            OntologyDataGrant.user_id == context.principal.user_id,
            OntologyDataGrant.status == "active",
        )
    ).scalars().all()
    user_entitlement = any(
        required_capability in (grant.capabilities or [])
        and (grant.valid_from is None or _as_aware_utc(grant.valid_from) <= now)
        and (grant.valid_until is None or now < _as_aware_utc(grant.valid_until))
        for grant in grants
    )
    if not user_entitlement:
        return _deny(ReasonCode.USER_ENTITLEMENT_DENIED, agent_capability=True, ontology_id=ontology_id)

    return PolicyDecision(
        allowed=True, reason_code=ReasonCode.ALLOW,
        agent_capability=True, user_entitlement=True, policy_evidence={},
    )
