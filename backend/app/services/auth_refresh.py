"""Rotating browser refresh families.

Login creates one `AuthRefreshFamily` plus a generation-0
`AuthRefreshToken` (storing only a SHA-256 hash, never the bearer).  A refresh
locks the token and family rows `FOR UPDATE`, reloads the current user/domain
authorization state, and commits exactly one successor generation.  Presenting
any generation below the family's current generation is reuse and revokes the
whole family; an inactive/deleted user or inactive domain revokes the family
and issues nothing.  Tokens are append-only; revocation mutates only the
family row.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth_refresh import AuthRefreshFamily, AuthRefreshToken
from app.services.auth_service import create_access_token, get_user_by_id

REFRESH_TOKEN_TTL_DAYS = 30


class RefreshSessionError(Exception):
    """Base refresh-session failure."""


class RefreshNotFoundError(RefreshSessionError):
    pass


class RefreshRevokedError(RefreshSessionError):
    pass


class RefreshReuseError(RefreshSessionError):
    pass


def issue_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _revoke_family(db: Session, family: AuthRefreshFamily) -> None:
    family.status = "revoked"
    family.revoked_at = datetime.now(timezone.utc)


def create_refresh_session(db: Session, user_id: str) -> str:
    """Create a fresh family at generation 0; returns the one-time plaintext token."""
    user = get_user_by_id(db, user_id)
    if user is None:
        raise RefreshNotFoundError("user not found")
    family_id = str(uuid.uuid4())
    token = issue_refresh_token()
    db.add(AuthRefreshFamily(
        id=family_id,
        user_id=user.id,
        security_domain_id=user.security_domain_id,
        current_generation=0,
        status="active",
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
    ))
    db.add(AuthRefreshToken(
        id=str(uuid.uuid4()),
        family_id=family_id,
        generation=0,
        token_hash=hash_refresh_token(token),
        status="active",
    ))
    db.commit()
    return token


def rotate_refresh_session(db: Session, refresh_token: str) -> tuple[str, str]:
    """Rotate one generation; returns (access_token, successor_refresh_token)."""
    digest = hash_refresh_token(refresh_token)
    token_row = db.execute(
        select(AuthRefreshToken)
        .where(AuthRefreshToken.token_hash == digest)
        .with_for_update()
    ).scalar_one_or_none()
    if token_row is None:
        raise RefreshNotFoundError("unknown refresh token")
    family = db.execute(
        select(AuthRefreshFamily)
        .where(AuthRefreshFamily.id == token_row.family_id)
        .with_for_update()
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if family is None or family.status != "active" or (family.expires_at and family.expires_at < now):
        raise RefreshRevokedError("refresh family revoked or expired")
    if token_row.generation != family.current_generation:
        _revoke_family(db, family)
        db.commit()
        raise RefreshReuseError("AUTH_REFRESH_REUSED: stale generation detected")
    user = get_user_by_id(db, family.user_id)
    if user is None or not user.is_active:
        _revoke_family(db, family)
        db.commit()
        raise RefreshRevokedError("user inactive or missing")
    successor = issue_refresh_token()
    next_generation = family.current_generation + 1
    db.add(AuthRefreshToken(
        id=str(uuid.uuid4()),
        family_id=family.id,
        generation=next_generation,
        token_hash=hash_refresh_token(successor),
        status="active",
    ))
    family.current_generation = next_generation
    db.commit()
    access_token = create_access_token({"sub": user.id, "role": user.role})
    return access_token, successor


def revoke_refresh_families(db: Session, user_id: str) -> None:
    """Revoke every active family for the user atomically; evidence stays append-only."""
    families = db.execute(
        select(AuthRefreshFamily)
        .where(AuthRefreshFamily.user_id == user_id, AuthRefreshFamily.status == "active")
        .with_for_update()
    ).scalars().all()
    for family in families:
        _revoke_family(db, family)
    db.commit()


def revoke_refresh_session(db: Session, refresh_token: str) -> None:
    """Revoke the family owning the presented token (logout)."""
    digest = hash_refresh_token(refresh_token)
    token_row = db.execute(
        select(AuthRefreshToken).where(AuthRefreshToken.token_hash == digest)
    ).scalar_one_or_none()
    if token_row is None:
        db.commit()
        return
    family = db.execute(
        select(AuthRefreshFamily)
        .where(AuthRefreshFamily.id == token_row.family_id)
        .with_for_update()
    ).scalar_one_or_none()
    if family is not None:
        _revoke_family(db, family)
    db.commit()
