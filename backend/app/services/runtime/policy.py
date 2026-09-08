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
from app.schemas.refresh import SourceCursor
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
    # Task 20: informational freshness exposure. `evaluate_access` computes
    # these against `DEFAULT_FRESHNESS_POLICY` for every caller so a denial
    # or an allow can both explain how fresh the pinned snapshot's source
    # data was — this never changes `allowed`. Reads and writes tolerate
    # staleness differently, so the actual ALLOW/HITL/DENY freshness
    # decision is made separately by the caller (`RuntimeService`) via
    # `evaluate_snapshot_freshness`.
    freshness_state: str | None = None
    freshness_lag_seconds: int | None = None
    source_cursor: dict[str, Any] | None = None


@dataclass(frozen=True)
class FreshnessPolicy:
    """How stale a snapshot's source data may be before Runtime refuses or
    requires human approval. `stale_action` governs only the soft-stale
    band (`lag_seconds <= hard_deny_after_seconds`, when set); a policy can
    never turn an `unknown` freshness state into an ALLOW."""

    max_lag_seconds: int
    hard_deny_after_seconds: int | None = None
    stale_action: str = "deny"  # "deny" | "human_approved"


@dataclass(frozen=True)
class FreshnessView:
    """Pure, point-in-time freshness projection of one snapshot, computed by
    `compute_snapshot_freshness`. Never mutates or is stored back onto the
    snapshot — the snapshot's own frozen fields are the historical facts;
    this is today's read of them against `now` and a policy."""

    state: str  # "fresh" | "stale" | "unknown"
    lag_seconds: int | None
    cursor: SourceCursor | None
    source_ids: tuple[str, ...] = ()
    dataset_version_ids: tuple[str, ...] = ()
    pipeline_run_ids: list[str] = field(default_factory=list)
    last_successful_refresh_run_id: str | None = None
    sla_status: str = "unknown"


@dataclass(frozen=True)
class FreshnessDecision:
    """The ALLOW/HUMAN_APPROVED/DENY verdict `evaluate_snapshot_freshness`
    computes from one `FreshnessView` and `FreshnessPolicy`."""

    decision: str  # "ALLOW" | "HUMAN_APPROVED" | "DENY"
    reason_code: str
    lag_seconds: int | None


# A shared default used both to decorate `PolicyDecision` (informational)
# and, by `RuntimeService`, to gate investigations/action plans. Soft-stale
# data may proceed to exact-plan HITL; hard-stale or unknown data is denied.
DEFAULT_FRESHNESS_POLICY = FreshnessPolicy(
    max_lag_seconds=3600, hard_deny_after_seconds=86400, stale_action="human_approved",
)


def cursor_to_dict(cursor: SourceCursor | None) -> dict[str, Any] | None:
    """The wire-safe dict shape for a `SourceCursor` (no protected content)."""
    if cursor is None:
        return None
    return {
        "source_id": cursor.source_id, "resource": cursor.resource, "contract": cursor.contract,
        "watermark": cursor.watermark, "primary_key": cursor.primary_key, "opaque_value": cursor.opaque_value,
        "observed_at": cursor.observed_at.isoformat() if cursor.observed_at else None,
    }


def _source_ids(snapshot: Any) -> tuple[str, ...]:
    lineage_summary = getattr(snapshot, "lineage_summary", None) or {}
    ids = set()
    source_id = lineage_summary.get("source_id")
    if isinstance(source_id, str) and source_id:
        ids.add(source_id)
    for citation in (getattr(snapshot, "evidence_summary", None) or {}).get("citations", []) or []:
        if isinstance(citation, dict) and isinstance(citation.get("source_id"), str):
            ids.add(citation["source_id"])
    return tuple(sorted(ids))


def compute_snapshot_freshness(
    snapshot: SnapshotView, *, now: datetime, policy: FreshnessPolicy,
) -> FreshnessView:
    """Pure function: read `snapshot`'s frozen freshness pins and classify
    them against `now`/`policy`. Never updates `snapshot`."""
    pipeline_run_ids = list(getattr(snapshot, "pipeline_run_ids", None) or [])
    dataset_version_ids = tuple(getattr(snapshot, "dataset_version_ids", None) or ())
    lineage_summary = getattr(snapshot, "lineage_summary", None) or {}
    last_run_id = lineage_summary.get("last_successful_refresh_run_id")
    source_cursor = getattr(snapshot, "source_cursor", None)
    stored_state = getattr(snapshot, "freshness_state", "unknown")
    observed_at_raw = (source_cursor or {}).get("observed_at") if source_cursor else None

    if stored_state == "unknown" or not source_cursor or not observed_at_raw:
        return FreshnessView(
            state="unknown", lag_seconds=None, cursor=None,
            source_ids=_source_ids(snapshot), dataset_version_ids=dataset_version_ids,
            pipeline_run_ids=pipeline_run_ids, last_successful_refresh_run_id=last_run_id,
            sla_status="unknown",
        )

    observed_at = (
        datetime.fromisoformat(observed_at_raw) if isinstance(observed_at_raw, str) else observed_at_raw
    )
    observed_at = _as_aware_utc(observed_at)
    lag_seconds = max(0, int((_as_aware_utc(now) - observed_at).total_seconds()))
    state = "fresh" if lag_seconds <= policy.max_lag_seconds else "stale"
    cursor = SourceCursor(
        source_id=source_cursor.get("source_id", ""), resource=source_cursor.get("resource", ""),
        contract=source_cursor.get("contract", ""), watermark=source_cursor.get("watermark"),
        primary_key=source_cursor.get("primary_key"), opaque_value=source_cursor.get("opaque_value"),
        observed_at=observed_at,
    )
    return FreshnessView(
        state=state, lag_seconds=lag_seconds, cursor=cursor,
        source_ids=_source_ids(snapshot), dataset_version_ids=dataset_version_ids,
        pipeline_run_ids=pipeline_run_ids, last_successful_refresh_run_id=last_run_id,
        sla_status="within_sla" if state == "fresh" else "breached",
    )


def evaluate_snapshot_freshness(freshness: FreshnessView, policy: FreshnessPolicy) -> FreshnessDecision:
    """Classify a `FreshnessView` into ALLOW/HUMAN_APPROVED/DENY. An unknown
    freshness state is always denied — no policy can make it silently fresh.

    `reason_code` here is `SNAPSHOT_STALE` for both a hard-stale and an
    unknown state — both are treated the same way by a write's DENY. A
    *read* (`RuntimeService.investigate`) additionally distinguishes
    "known-stale" from "unknown provenance" using `freshness.state` itself
    (see its own `SNAPSHOT_STALE`/`SNAPSHOT_NOT_GOVERNED` split), rather
    than this function's `reason_code`.
    """
    if freshness.state == "unknown":
        return FreshnessDecision(
            decision="DENY", reason_code=ReasonCode.SNAPSHOT_STALE.value,
            lag_seconds=freshness.lag_seconds,
        )
    if freshness.state == "fresh":
        return FreshnessDecision(
            decision="ALLOW", reason_code=ReasonCode.ALLOW.value, lag_seconds=freshness.lag_seconds,
        )
    lag_seconds = freshness.lag_seconds or 0
    if policy.hard_deny_after_seconds is not None and lag_seconds > policy.hard_deny_after_seconds:
        return FreshnessDecision(
            decision="DENY", reason_code=ReasonCode.SNAPSHOT_STALE.value, lag_seconds=lag_seconds,
        )
    if policy.stale_action == "human_approved":
        return FreshnessDecision(
            decision="HUMAN_APPROVED", reason_code=ReasonCode.SNAPSHOT_FRESHNESS_HITL.value,
            lag_seconds=lag_seconds,
        )
    return FreshnessDecision(decision="DENY", reason_code=ReasonCode.SNAPSHOT_STALE.value, lag_seconds=lag_seconds)


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite round-trips naive datetimes; every value this module compares
    against is UTC, so a naive read is always UTC too."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _deny(reason_code: ReasonCode, *, agent_capability: bool = False,
          user_entitlement: bool = False, freshness: FreshnessView | None = None,
          **evidence: Any) -> PolicyDecision:
    return PolicyDecision(
        allowed=False, reason_code=reason_code,
        agent_capability=agent_capability, user_entitlement=user_entitlement,
        policy_evidence=evidence,
        freshness_state=freshness.state if freshness else None,
        freshness_lag_seconds=freshness.lag_seconds if freshness else None,
        source_cursor=cursor_to_dict(freshness.cursor) if freshness else None,
    )


def evaluate_access(
    context: RuntimeContext, *, required_capability: str, snapshot: SnapshotView,
    ontology_id: str, db: Session,
) -> PolicyDecision:
    """Compute Agent capability ∩ user entitlement ∩ runtime policy.

    Validates the snapshot's governing release and ontology identity before
    any capability/entitlement lookup, then evaluates checks in a fixed
    order so the first failing layer determines `reason_code`: snapshot
    governance, runtime policy (cross-domain and live active-status
    re-check), Agent capability, user entitlement. Snapshot freshness
    (Task 20) is computed here only to decorate the returned
    `PolicyDecision` with informational `freshness_state`/`lag`/
    `source_cursor` fields on every path (allow or deny) — it never flips
    `allowed`. Reads and writes tolerate staleness differently (a read is
    denied outright, a write may still proceed to exact-plan HITL), so the
    actual freshness ALLOW/HUMAN_APPROVED/DENY routing is applied
    separately by `RuntimeService` via `evaluate_snapshot_freshness`.
    """
    release = db.execute(
        select(OntologyRelease).where(OntologyRelease.id == snapshot.ontology_release_id)
    ).scalar_one_or_none()
    if release is None or release.ontology_id != ontology_id:
        return _deny(ReasonCode.SNAPSHOT_NOT_GOVERNED, ontology_release_id=snapshot.ontology_release_id)

    freshness = compute_snapshot_freshness(
        snapshot, now=datetime.now(timezone.utc), policy=DEFAULT_FRESHNESS_POLICY,
    )

    project = db.execute(
        select(OntologyProject).where(OntologyProject.id == release.ontology_id)
    ).scalar_one_or_none()
    if project is None or release.status != "published" or project.latest_published_release_id != release.id:
        return _deny(ReasonCode.SNAPSHOT_STALE, ontology_release_id=release.id, freshness=freshness)

    if project.security_domain_id != context.principal.security_domain_id:
        return _deny(ReasonCode.CROSS_SECURITY_DOMAIN, ontology_id=project.id, freshness=freshness)

    client = db.execute(
        select(OAuthClient).where(OAuthClient.id == context.principal.agent_id)
    ).scalar_one_or_none()
    user = db.execute(
        select(User).where(User.id == context.principal.user_id)
    ).scalar_one_or_none()
    if client is None or user is None or not client.is_active or not user.is_active:
        return _deny(ReasonCode.POLICY_DENIED, freshness=freshness)

    agent_capability = required_capability in (client.capability_names or [])
    if not agent_capability:
        return _deny(ReasonCode.AGENT_CAPABILITY_DENIED, agent_id=client.id, freshness=freshness)

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
        return _deny(
            ReasonCode.USER_ENTITLEMENT_DENIED, agent_capability=True, ontology_id=ontology_id,
            freshness=freshness,
        )

    return PolicyDecision(
        allowed=True, reason_code=ReasonCode.ALLOW,
        agent_capability=True, user_entitlement=True, policy_evidence={},
        freshness_state=freshness.state, freshness_lag_seconds=freshness.lag_seconds,
        source_cursor=cursor_to_dict(freshness.cursor),
    )
