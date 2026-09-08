"""OAuth 2.0 Authorization Code + PKCE flow (RFC 6749 / RFC 7636).

Mirrors the interactive `auth_refresh` rotating-family design: a refresh
session is one `OAuthRefreshFamily` at a `current_generation`, backed by
append-only `OAuthRefreshToken` rows storing only a SHA-256 hash. An
authorization code is single-use, hashed at rest, and PKCE-bound (S256
only) instead of a client secret, since public clients (native apps, MCP
clients) cannot keep a secret confidential.
"""
import base64
import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.oauth import OAuthAuthorizationCode, OAuthClient, OAuthRefreshFamily, OAuthRefreshToken
from app.services.auth_service import create_oauth_access_token, get_user_by_id

CODE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
CODE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9\-_]{43}$")


class OAuthFlowError(Exception):
    """Base flow failure; `error` is the RFC 6749 §5.2 machine-readable code."""

    def __init__(self, error: str, description: str):
        self.error = error
        self.description = description
        super().__init__(description)


class InvalidRequestError(OAuthFlowError):
    def __init__(self, description: str):
        super().__init__("invalid_request", description)


class InvalidGrantError(OAuthFlowError):
    def __init__(self, description: str):
        super().__init__("invalid_grant", description)


def validate_code_challenge(code_challenge: str, method: str) -> None:
    if method != "S256":
        raise InvalidRequestError("code_challenge_method must be S256")
    if not CODE_CHALLENGE_RE.match(code_challenge):
        raise InvalidRequestError("malformed code_challenge")


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    if not CODE_VERIFIER_RE.match(code_verifier):
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return secrets.compare_digest(computed, code_challenge)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_authorization_code(
    db: Session, *, client_id: str, user_id: str, redirect_uri: str,
    code_challenge: str, code_challenge_method: str, scope: str,
) -> str:
    validate_code_challenge(code_challenge, code_challenge_method)
    code = secrets.token_urlsafe(32)
    db.add(OAuthAuthorizationCode(
        id=str(uuid.uuid4()), code_hash=_hash(code), client_id=client_id, user_id=user_id,
        redirect_uri=redirect_uri, code_challenge=code_challenge, code_challenge_method=code_challenge_method,
        scope=scope, expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.oauth_authorization_code_expire_seconds),
    ))
    db.commit()
    return code


def exchange_authorization_code(
    db: Session, *, code: str, client_id: str, redirect_uri: str, code_verifier: str,
) -> tuple[str, str, str, int]:
    """Returns (access_token, refresh_token, scope, expires_in)."""
    client = db.execute(select(OAuthClient).where(OAuthClient.id == client_id)).scalar_one_or_none()
    if client is None or not client.is_active:
        raise InvalidGrantError("unknown or inactive client")
    row = db.execute(
        select(OAuthAuthorizationCode).where(OAuthAuthorizationCode.code_hash == _hash(code)).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise InvalidGrantError("unknown authorization code")
    if row.used_at is not None:
        raise InvalidGrantError("authorization code already used")
    now = datetime.now(timezone.utc)
    if row.expires_at < now:
        raise InvalidGrantError("authorization code expired")
    if row.client_id != client_id:
        raise InvalidGrantError("client_id mismatch")
    if row.redirect_uri != redirect_uri:
        raise InvalidGrantError("redirect_uri mismatch")
    if not _verify_pkce(code_verifier, row.code_challenge):
        raise InvalidGrantError("PKCE verification failed")
    row.used_at = now
    user = get_user_by_id(db, row.user_id)
    if user is None or not user.is_active:
        db.commit()
        raise InvalidGrantError("user inactive or missing")
    family_id = str(uuid.uuid4())
    refresh_token = secrets.token_urlsafe(48)
    db.add(OAuthRefreshFamily(
        id=family_id, client_id=client_id, user_id=user.id, security_domain_id=user.security_domain_id,
        scope=row.scope, current_generation=0, status="active",
        expires_at=now + timedelta(days=settings.oauth_refresh_token_expire_days),
    ))
    db.add(OAuthRefreshToken(
        id=str(uuid.uuid4()), family_id=family_id, generation=0, token_hash=_hash(refresh_token), status="active",
    ))
    db.commit()
    access_token = create_oauth_access_token(user.id, client_id, row.scope)
    return access_token, refresh_token, row.scope, settings.oauth_access_token_expire_minutes * 60


def rotate_oauth_refresh(db: Session, *, refresh_token: str, client_id: str) -> tuple[str, str, str, int]:
    """Returns (access_token, successor_refresh_token, scope, expires_in)."""
    client = db.execute(select(OAuthClient).where(OAuthClient.id == client_id)).scalar_one_or_none()
    if client is None or not client.is_active:
        raise InvalidGrantError("unknown or inactive client")
    digest = _hash(refresh_token)
    token_row = db.execute(
        select(OAuthRefreshToken).where(OAuthRefreshToken.token_hash == digest).with_for_update()
    ).scalar_one_or_none()
    if token_row is None:
        raise InvalidGrantError("unknown refresh token")
    family = db.execute(
        select(OAuthRefreshFamily).where(OAuthRefreshFamily.id == token_row.family_id).with_for_update()
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if family is None or family.status != "active" or family.expires_at < now:
        raise InvalidGrantError("refresh session revoked or expired")
    if family.client_id != client_id:
        raise InvalidGrantError("client_id mismatch")
    if token_row.generation != family.current_generation:
        family.status = "revoked"
        family.revoked_at = now
        db.commit()
        raise InvalidGrantError("refresh token reuse detected")
    user = get_user_by_id(db, family.user_id)
    if user is None or not user.is_active:
        family.status = "revoked"
        family.revoked_at = now
        db.commit()
        raise InvalidGrantError("user inactive or missing")
    successor = secrets.token_urlsafe(48)
    next_generation = family.current_generation + 1
    db.add(OAuthRefreshToken(
        id=str(uuid.uuid4()), family_id=family.id, generation=next_generation,
        token_hash=_hash(successor), status="active",
    ))
    family.current_generation = next_generation
    db.commit()
    access_token = create_oauth_access_token(user.id, family.client_id, family.scope)
    return access_token, successor, family.scope, settings.oauth_access_token_expire_minutes * 60


def revoke_oauth_refresh(db: Session, *, refresh_token: str, client_id: str) -> None:
    """Revoke the family owning the presented token; a no-op (still success) if unknown, per RFC 7009."""
    digest = _hash(refresh_token)
    token_row = db.execute(select(OAuthRefreshToken).where(OAuthRefreshToken.token_hash == digest)).scalar_one_or_none()
    if token_row is None:
        db.commit()
        return
    family = db.execute(
        select(OAuthRefreshFamily).where(OAuthRefreshFamily.id == token_row.family_id).with_for_update()
    ).scalar_one_or_none()
    if family is not None and family.client_id == client_id and family.status == "active":
        family.status = "revoked"
        family.revoked_at = datetime.now(timezone.utc)
    db.commit()
