"""Task 13: trusted delegated credentials and Runtime context.

Every Runtime request must present a short-lived, signed credential —
minted with OAuth 2.0 token-exchange semantics — that resolves to two
*verified* principals: the calling registered Agent/service identity and
the user it is delegated to act for. Each parametrized denial case is a
distinct way a forged, stale, or cross-tenant credential could otherwise
slip a caller-asserted identity past the Runtime boundary.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.deps.runtime import authorize_request
from app.models.oauth import OAuthClient
from app.models.runtime_identity import RuntimeDelegatedCredential
from app.models.user import User
from app.services.auth_service import hash_password
from app.services.runtime.credentials import (
    RUNTIME_DENIAL_CODES,
    RuntimeAccessError,
    hash_delegated_token,
    issue_delegated_credential,
    revoke_delegated_credential,
    verify_delegated_credential,
)

FIXED_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
AUDIENCE = "ontexus-runtime"
SCOPE = "ontology:read"
DOMAIN_A = "00000000-0000-0000-0000-0000000000a1"
DOMAIN_B = "00000000-0000-0000-0000-0000000000b2"


def _seed_user(db, *, user_id="user-001", security_domain_id=DOMAIN_A, is_active=True):
    user = User(
        id=user_id, username=user_id, email=f"{user_id}@example.invalid",
        password_hash=hash_password("x"), role="viewer", is_active=is_active,
        security_domain_id=security_domain_id,
    )
    db.add(user)
    return user


def _seed_client(db, *, client_id="agent-service-001", security_domain_id=DOMAIN_A, is_active=True):
    client = OAuthClient(
        id=client_id, client_name="Test Agent Service", redirect_uris=[],
        allowed_scopes=[SCOPE], is_active=is_active, created_by="user-001",
        security_domain_id=security_domain_id, allowed_audiences=[AUDIENCE],
        capability_names=["investigate"],
    )
    db.add(client)
    return client


def _issue_valid_token(db, *, ttl_seconds=300) -> str:
    _seed_user(db)
    _seed_client(db)
    db.commit()
    return issue_delegated_credential(
        db, client_id="agent-service-001", user_id="user-001",
        audience=AUDIENCE, scope={SCOPE}, ttl_seconds=ttl_seconds, now=FIXED_NOW,
    )


def test_valid_delegation_contains_two_verified_principals(db):
    token = _issue_valid_token(db)
    context = verify_delegated_credential(
        db, token, audience=AUDIENCE, required_scope=SCOPE, now=FIXED_NOW,
    )
    assert context.principal.agent_id == "agent-service-001"
    assert context.principal.user_id == "user-001"
    assert context.principal.security_domain_id == DOMAIN_A
    assert context.principal.audience == AUDIENCE
    assert context.principal.scope == frozenset({SCOPE})
    assert context.principal.token_id
    assert context.correlation_id


def verify_fixture_credential(db, case_id: str):
    """Build the one scenario `case_id` denotes, then attempt verification —
    the resulting RuntimeAccessError (or lack of one) is the assertion."""
    audience = AUDIENCE
    required_scope = SCOPE
    verify_now = FIXED_NOW

    if case_id == "missing":
        token = None
    elif case_id == "bad-signature":
        token = _issue_valid_token(db)
        # Flip the last character of the signature segment; still base64url,
        # still three dot-separated segments, but no longer a valid MAC.
        header_payload, sig = token.rsplit(".", 1)
        tampered_char = "A" if sig[-1] != "A" else "B"
        token = f"{header_payload}.{sig[:-1]}{tampered_char}"
    elif case_id == "expired":
        token = _issue_valid_token(db, ttl_seconds=1)
        verify_now = FIXED_NOW + timedelta(seconds=2)
    elif case_id == "wrong-audience":
        token = _issue_valid_token(db)
        audience = "some-other-audience"
    elif case_id == "missing-scope":
        token = _issue_valid_token(db)
        required_scope = "ontology:write"
    elif case_id == "revoked":
        token = _issue_valid_token(db)
        record = db.execute(
            select(RuntimeDelegatedCredential).where(
                RuntimeDelegatedCredential.token_hash == hash_delegated_token(token)
            )
        ).scalar_one()
        revoke_delegated_credential(db, token_id=record.id, now=FIXED_NOW)
    elif case_id == "inactive-agent":
        token = _issue_valid_token(db)
        client = db.get(OAuthClient, "agent-service-001")
        client.is_active = False
        db.commit()
    elif case_id == "inactive-user":
        token = _issue_valid_token(db)
        user = db.get(User, "user-001")
        user.is_active = False
        db.commit()
    elif case_id == "cross-domain":
        token = _issue_valid_token(db)
        # Domain reassignment after issuance: valid at issuance time, but
        # verification must re-check live state, not trust the credential.
        user = db.get(User, "user-001")
        user.security_domain_id = DOMAIN_B
        db.commit()
    else:  # pragma: no cover - guards against a typo in the parametrize list
        raise AssertionError(f"unknown case_id: {case_id}")

    return verify_delegated_credential(
        db, token, audience=audience, required_scope=required_scope, now=verify_now,
    )


@pytest.mark.parametrize("case_id", [
    "missing", "bad-signature", "wrong-audience", "missing-scope",
    "expired", "revoked", "inactive-agent", "inactive-user", "cross-domain",
])
def test_invalid_delegation_is_structured_denial(db, case_id):
    with pytest.raises(RuntimeAccessError) as exc:
        verify_fixture_credential(db, case_id)
    assert exc.value.reason_code in RUNTIME_DENIAL_CODES


def test_body_identity_cannot_override_credential(db):
    token = _issue_valid_token(db)
    context = verify_delegated_credential(db, token, audience=AUDIENCE, required_scope=SCOPE, now=FIXED_NOW)
    principal = authorize_request(context, body_agent_id="agent-other", body_user_id="user-other")
    assert principal.agent_id == context.principal.agent_id
    assert principal.user_id == context.principal.user_id
    assert principal.agent_id != "agent-other"
    assert principal.user_id != "user-other"


def test_issuance_rejects_cross_domain_pairing(db):
    """Issuance binds actor and user to the same security domain up front —
    a credential is never minted for a pairing verification would reject."""
    _seed_user(db, security_domain_id=DOMAIN_A)
    _seed_client(db, security_domain_id=DOMAIN_B)
    db.commit()
    with pytest.raises(RuntimeAccessError) as exc:
        issue_delegated_credential(
            db, client_id="agent-service-001", user_id="user-001",
            audience=AUDIENCE, scope={SCOPE}, ttl_seconds=300, now=FIXED_NOW,
        )
    assert exc.value.reason_code == "CROSS_SECURITY_DOMAIN"


def test_issuance_rejects_unregistered_audience(db):
    _seed_user(db)
    _seed_client(db)
    db.commit()
    with pytest.raises(RuntimeAccessError) as exc:
        issue_delegated_credential(
            db, client_id="agent-service-001", user_id="user-001",
            audience="unregistered-audience", scope={SCOPE}, ttl_seconds=300, now=FIXED_NOW,
        )
    assert exc.value.reason_code == "AUDIENCE_DENIED"


def test_delegated_token_hash_is_stored_not_plaintext(db):
    token = _issue_valid_token(db)
    record = db.execute(
        select(RuntimeDelegatedCredential).where(
            RuntimeDelegatedCredential.token_hash == hash_delegated_token(token)
        )
    ).scalar_one()
    assert record.token_hash != token


def test_oauth_access_token_cannot_be_replayed_as_a_delegated_credential(db):
    """A same-secret, same-algorithm token minted for a different purpose
    (here, the existing interactive OAuth access token) must not verify as
    a Runtime delegated credential — signature validity alone is not
    sufficient, `token_use`/`iss` must also match."""
    from app.services.auth_service import create_oauth_access_token

    _seed_user(db)
    _seed_client(db)
    db.commit()
    oauth_access_token = create_oauth_access_token("user-001", "agent-service-001", SCOPE)
    with pytest.raises(RuntimeAccessError) as exc:
        verify_delegated_credential(db, oauth_access_token, audience=AUDIENCE, required_scope=SCOPE, now=FIXED_NOW)
    assert exc.value.reason_code == "INVALID_DELEGATION"
