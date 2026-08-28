"""Task 14: shared intersection-policy evaluator.

`evaluate_access` computes Agent capability (`OAuthClient.capability_names`)
∩ user entitlement (`OntologyDataGrant.capabilities`, scoped to the
ontology and its validity window) ∩ runtime policy (a live active-status
re-check of both principals). Each denial case below isolates exactly one
missing side of that intersection so `reason_code` stays a reliable,
distinguishing signal for callers.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.oauth import OAuthClient
from app.models.ontology import OntologyProject
from app.models.ontology_data_grant import OntologyDataGrant
from app.schemas.runtime_snapshot import SnapshotView
from app.services.runtime.credentials import RuntimeContext, RuntimePrincipal
from app.services.runtime.policy import PolicyDecision, evaluate_access

REQUIRED_CAPABILITY = "investigate"


def _client(db, *, client_id="agent-001", security_domain_id, capability_names=(REQUIRED_CAPABILITY,), is_active=True):
    client = OAuthClient(
        id=client_id, client_name="Test Agent", redirect_uris=[], allowed_scopes=[],
        is_active=is_active, created_by="user-001", security_domain_id=security_domain_id,
        allowed_audiences=[], capability_names=list(capability_names),
    )
    db.add(client)
    return client


def _grant(db, *, ontology_id, user_id="user-001", capabilities=(REQUIRED_CAPABILITY,),
           status="active", valid_from=None, valid_until=None):
    grant = OntologyDataGrant(
        id=str(uuid.uuid4()), ontology_id=ontology_id, user_id=user_id,
        capabilities=list(capabilities), status=status, created_by=user_id,
        valid_from=valid_from, valid_until=valid_until,
    )
    db.add(grant)
    return grant


def _context(*, agent_id="agent-001", user_id="user-001", security_domain_id):
    principal = RuntimePrincipal(
        agent_id=agent_id, user_id=user_id, security_domain_id=security_domain_id,
        audience="ontexus-runtime", scope=frozenset({"ontology:read"}), token_id="tok-001",
    )
    return RuntimeContext(principal=principal, correlation_id="corr-001")


def _snapshot(release_id="release-valid-001", snapshot_id="snap-valid-001"):
    return SnapshotView(
        id=snapshot_id, ontology_release_id=release_id,
        materialization_hash="a" * 64, status="materialized", created_by="user-001",
    )


def evaluate_fixture_policy(db, snapshot_user, valid_release, case_id: str) -> PolicyDecision:
    """Build the one scenario `case_id` denotes against real rows, then
    evaluate access — the resulting PolicyDecision is the assertion."""
    domain = snapshot_user.security_domain_id
    ontology_id = valid_release.ontology_id

    if case_id == "agent-only":
        _client(db, security_domain_id=domain, capability_names=[REQUIRED_CAPABILITY])
        # no entitlement grant for the user at all
    elif case_id == "user-only":
        _client(db, security_domain_id=domain, capability_names=[])
        _grant(db, ontology_id=ontology_id)
    elif case_id == "policy-denied":
        _client(db, security_domain_id=domain, capability_names=[REQUIRED_CAPABILITY], is_active=False)
        _grant(db, ontology_id=ontology_id)
    elif case_id == "snapshot-stale":
        _client(db, security_domain_id=domain, capability_names=[REQUIRED_CAPABILITY])
        _grant(db, ontology_id=ontology_id)
        project = db.get(OntologyProject, ontology_id)
        project.latest_published_release_id = "release-superseded-999"
    else:  # pragma: no cover - guards against a typo in the parametrize list
        raise AssertionError(f"unknown case_id: {case_id}")
    db.commit()

    return evaluate_access(
        _context(security_domain_id=domain), required_capability=REQUIRED_CAPABILITY,
        snapshot=_snapshot(release_id=valid_release.id), ontology_id=ontology_id, db=db,
    )


@pytest.mark.parametrize("case_id", [
    "agent-only", "user-only", "policy-denied", "snapshot-stale",
])
def test_intersection_policy_denies_with_stable_reason(db, snapshot_user, valid_release, case_id):
    decision = evaluate_fixture_policy(db, snapshot_user, valid_release, case_id)
    assert not decision.allowed
    assert decision.reason_code in {
        "AGENT_CAPABILITY_DENIED", "USER_ENTITLEMENT_DENIED",
        "POLICY_DENIED", "SNAPSHOT_STALE",
    }


def test_full_intersection_allows(db, snapshot_user, valid_release):
    domain = snapshot_user.security_domain_id
    ontology_id = valid_release.ontology_id
    _client(db, security_domain_id=domain, capability_names=[REQUIRED_CAPABILITY])
    _grant(db, ontology_id=ontology_id)
    db.commit()

    decision = evaluate_access(
        _context(security_domain_id=domain), required_capability=REQUIRED_CAPABILITY,
        snapshot=_snapshot(release_id=valid_release.id), ontology_id=ontology_id, db=db,
    )
    assert decision.allowed
    assert decision.reason_code == "ALLOW"
    assert decision.agent_capability is True
    assert decision.user_entitlement is True


def test_snapshot_not_governed_denies_before_any_capability_check(db, snapshot_user, valid_release):
    """A caller asserting the wrong ontology_id for a real snapshot must be
    rejected before any capability/entitlement row is even consulted —
    otherwise a snapshot could be laundered through an unrelated ontology's
    entitlements."""
    domain = snapshot_user.security_domain_id
    # No Agent/user rows are seeded at all; if capability/entitlement were
    # checked first this would fail for the wrong reason.
    decision = evaluate_access(
        _context(security_domain_id=domain), required_capability=REQUIRED_CAPABILITY,
        snapshot=_snapshot(release_id=valid_release.id), ontology_id="ontology-unrelated", db=db,
    )
    assert not decision.allowed
    assert decision.reason_code == "SNAPSHOT_NOT_GOVERNED"


def test_cross_security_domain_denies_before_capability_checks(db, snapshot_user, valid_release):
    """A verified credential only proves the Agent and user share one
    domain; the queried ontology must independently share it too — even a
    fully capable/entitled principal from a foreign domain must be denied."""
    other_domain = "00000000-0000-0000-0000-0000000000ff"
    ontology_id = valid_release.ontology_id
    _client(db, security_domain_id=other_domain, capability_names=[REQUIRED_CAPABILITY])
    _grant(db, ontology_id=ontology_id)
    db.commit()

    decision = evaluate_access(
        _context(security_domain_id=other_domain), required_capability=REQUIRED_CAPABILITY,
        snapshot=_snapshot(release_id=valid_release.id), ontology_id=ontology_id, db=db,
    )
    assert not decision.allowed
    assert decision.reason_code == "CROSS_SECURITY_DOMAIN"


def test_entitlement_outside_validity_window_is_denied(db, snapshot_user, valid_release):
    domain = snapshot_user.security_domain_id
    ontology_id = valid_release.ontology_id
    _client(db, security_domain_id=domain, capability_names=[REQUIRED_CAPABILITY])
    _grant(
        db, ontology_id=ontology_id,
        valid_from=datetime.now(timezone.utc) - timedelta(days=10),
        valid_until=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.commit()

    decision = evaluate_access(
        _context(security_domain_id=domain), required_capability=REQUIRED_CAPABILITY,
        snapshot=_snapshot(release_id=valid_release.id), ontology_id=ontology_id, db=db,
    )
    assert not decision.allowed
    assert decision.reason_code == "USER_ENTITLEMENT_DENIED"


def test_denial_never_carries_protected_evidence(db, snapshot_user, valid_release):
    """PolicyDecision has no field capable of carrying query rows — a
    denial's policy_evidence is limited to identifiers, never row content."""
    decision = evaluate_fixture_policy(db, snapshot_user, valid_release, "agent-only")
    assert not decision.allowed
    for value in decision.policy_evidence.values():
        assert not isinstance(value, (list, dict))
