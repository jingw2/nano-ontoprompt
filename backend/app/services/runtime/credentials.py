"""Trusted delegated runtime credentials (Task 13, RFC 8693 OAuth 2.0 Token
Exchange semantics).

Every Runtime request must present a short-lived, signed credential that
carries two *verified* principals: the calling registered Agent/service
identity (an `OAuthClient`, RFC 8693's `act` claim) and the user it is
delegated to act for (the `sub` claim). Mirrors `auth_service`'s HS256
signing convention and `oauth_flow`'s hashed-at-rest storage
(`RuntimeDelegatedCredential`) — the plaintext credential is never
persisted, and the caller's own request body carries no authority: every
identity fact returned by `verify_delegated_credential` is re-derived from
the signed token and re-checked against live database state (active
client, active user, matching security domain) on every call, never from
anything the caller merely asserts.
"""
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.oauth import OAuthClient
from app.models.runtime_identity import RuntimeDelegatedCredential
from app.services.auth_service import get_user_by_id

RUNTIME_TOKEN_USE = "runtime_delegated"
RUNTIME_ISSUER = "ontexus-runtime-issuer"

RUNTIME_DENIAL_CODES = frozenset({
    "MISSING_DELEGATION",
    "INVALID_DELEGATION",
    "AUDIENCE_DENIED",
    "SCOPE_DENIED",
    "EXPIRED_DELEGATION",
    "REVOKED_DELEGATION",
    "AGENT_INACTIVE",
    "USER_INACTIVE",
    "CROSS_SECURITY_DOMAIN",
})


class RuntimeAccessError(Exception):
    """Structured denial for a delegated runtime credential.

    `reason_code` is always one of RUNTIME_DENIAL_CODES — stable, safe to
    log or return to a caller verbatim, and never derived from anything the
    caller supplied.
    """

    def __init__(self, reason_code: str, message: str | None = None):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


@dataclass(frozen=True)
class RuntimePrincipal:
    agent_id: str
    user_id: str
    security_domain_id: str
    audience: str
    scope: frozenset[str]
    token_id: str


@dataclass(frozen=True)
class RuntimeContext:
    principal: RuntimePrincipal
    correlation_id: str


def hash_delegated_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite (the unit-test harness) round-trips `DateTime(timezone=True)`
    values as naive; PostgreSQL preserves tzinfo. Every value this module
    ever writes is UTC, so a naive read is always UTC too."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _load_active_client(db: Session, client_id: str) -> OAuthClient | None:
    client = db.execute(select(OAuthClient).where(OAuthClient.id == client_id)).scalar_one_or_none()
    if client is None or not client.is_active:
        return None
    return client


def issue_delegated_credential(
    db: Session, *, client_id: str, user_id: str, audience: str,
    scope: set[str], ttl_seconds: int, now: datetime,
) -> str:
    """Issue a short-lived delegated credential binding `client_id` (the
    calling Agent/service identity) to `user_id` (the delegated user).

    Validates the registered identity's own audience/domain allowlists up
    front — the same checks `verify_delegated_credential` re-applies at
    presentation time — so a credential is never issued for a pairing that
    verification would immediately reject.
    """
    client = _load_active_client(db, client_id)
    if client is None:
        raise RuntimeAccessError("AGENT_INACTIVE")
    user = get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise RuntimeAccessError("USER_INACTIVE")
    if client.security_domain_id != user.security_domain_id:
        raise RuntimeAccessError("CROSS_SECURITY_DOMAIN")
    if audience not in (client.allowed_audiences or []):
        raise RuntimeAccessError("AUDIENCE_DENIED")
    if not scope.issubset(set(client.allowed_scopes or [])):
        raise RuntimeAccessError("SCOPE_DENIED")

    token_id = str(uuid.uuid4())
    expires_at = now + timedelta(seconds=ttl_seconds)
    scope_str = " ".join(sorted(scope))
    token = jwt.encode(
        {
            "iss": RUNTIME_ISSUER,
            "act": {"sub": client_id},
            "sub": user_id,
            "aud": audience,
            "scope": scope_str,
            "token_use": RUNTIME_TOKEN_USE,
            "jti": token_id,
            "iat": now,
            "exp": expires_at,
        },
        settings.secret_key, algorithm="HS256",
    )
    db.add(RuntimeDelegatedCredential(
        id=token_id, token_hash=hash_delegated_token(token), client_id=client_id, user_id=user_id,
        audience=audience, scope=scope_str, status="active", expires_at=expires_at, issued_at=now,
    ))
    db.commit()
    return token


def revoke_delegated_credential(db: Session, *, token_id: str, now: datetime) -> None:
    """Revoke a previously issued credential by its `jti`; a no-op if unknown."""
    record = db.execute(
        select(RuntimeDelegatedCredential).where(RuntimeDelegatedCredential.id == token_id)
    ).scalar_one_or_none()
    if record is None:
        return
    record.status = "revoked"
    record.revoked_at = now
    db.commit()


def verify_delegated_credential(
    db: Session, token: str | None, *, audience: str, required_scope: str, now: datetime,
) -> RuntimeContext:
    """Verify a delegated credential and return its two verified principals.

    Checks, in order: presence, issuer/signature, well-formed actor+subject
    claims, the persisted record's revocation status, expiry (against the
    caller-supplied `now`, never wall-clock), audience, scope, the
    registered Agent/service identity's active status, the delegated user's
    active status, and finally that both share one security domain. No
    caller-supplied identity is ever consulted — only the signed token and
    the database rows it points at.
    """
    if not token:
        raise RuntimeAccessError("MISSING_DELEGATION")
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=["HS256"],
            options={"verify_exp": False, "verify_aud": False},
        )
    except JWTError:
        raise RuntimeAccessError("INVALID_DELEGATION")
    if payload.get("token_use") != RUNTIME_TOKEN_USE or payload.get("iss") != RUNTIME_ISSUER:
        raise RuntimeAccessError("INVALID_DELEGATION")
    act = payload.get("act") or {}
    client_id = act.get("sub")
    user_id = payload.get("sub")
    token_id = payload.get("jti")
    if not client_id or not user_id or not token_id:
        raise RuntimeAccessError("INVALID_DELEGATION")

    record = db.execute(
        select(RuntimeDelegatedCredential).where(RuntimeDelegatedCredential.token_hash == hash_delegated_token(token))
    ).scalar_one_or_none()
    if record is None:
        raise RuntimeAccessError("INVALID_DELEGATION")
    if record.status == "revoked":
        raise RuntimeAccessError("REVOKED_DELEGATION")
    if _as_aware_utc(record.expires_at) <= now:
        raise RuntimeAccessError("EXPIRED_DELEGATION")
    if record.audience != audience:
        raise RuntimeAccessError("AUDIENCE_DENIED")
    granted_scope = set(record.scope.split())
    if required_scope not in granted_scope:
        raise RuntimeAccessError("SCOPE_DENIED")

    client = _load_active_client(db, record.client_id)
    if client is None:
        raise RuntimeAccessError("AGENT_INACTIVE")
    user = get_user_by_id(db, record.user_id)
    if user is None or not user.is_active:
        raise RuntimeAccessError("USER_INACTIVE")
    if client.security_domain_id != user.security_domain_id:
        raise RuntimeAccessError("CROSS_SECURITY_DOMAIN")

    principal = RuntimePrincipal(
        agent_id=client.id, user_id=user.id, security_domain_id=user.security_domain_id,
        audience=audience, scope=frozenset(granted_scope), token_id=record.id,
    )
    return RuntimeContext(principal=principal, correlation_id=str(uuid.uuid4()))
