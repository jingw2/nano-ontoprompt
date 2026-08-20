"""Admin-registered OAuth clients (v1: static registration only — see the
oauth-pkce-authorization-server plan's Global Constraints for why dynamic
client registration, RFC 7591, is explicitly out of scope)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.oauth import OAuthClient, OAuthRefreshFamily


def create_client(
    db: Session, *, client_name: str, redirect_uris: list[str],
    allowed_scopes: list[str], created_by: str,
) -> OAuthClient:
    client = OAuthClient(
        id=str(uuid.uuid4()), client_name=client_name, redirect_uris=redirect_uris,
        allowed_scopes=allowed_scopes, is_active=True, created_by=created_by,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def get_client(db: Session, client_id: str) -> OAuthClient | None:
    return db.execute(select(OAuthClient).where(OAuthClient.id == client_id)).scalar_one_or_none()


def list_clients(db: Session) -> list[OAuthClient]:
    return list(db.execute(select(OAuthClient).order_by(OAuthClient.created_at.desc())).scalars().all())


def deactivate_client(db: Session, client_id: str) -> None:
    client = get_client(db, client_id)
    if client is None:
        return
    client.is_active = False
    db.execute(
        update(OAuthRefreshFamily)
        .where(OAuthRefreshFamily.client_id == client_id, OAuthRefreshFamily.status == "active")
        .values(status="revoked", revoked_at=datetime.now(timezone.utc))
    )
    db.commit()


def validate_redirect_uri(client: OAuthClient, redirect_uri: str) -> bool:
    return redirect_uri in (client.redirect_uris or [])


def resolve_scope(client: OAuthClient, requested_scope: str | None) -> str:
    """Intersect the requested scope against the client's allowlist.

    A falsy `requested_scope` (None or empty string) returns the client's
    full allowlist. Raises ValueError if the request contains anything
    outside the client's allowed_scopes.
    """
    allowed = set(client.allowed_scopes or [])
    if not requested_scope:
        return " ".join(sorted(allowed))
    requested = set(requested_scope.split())
    if not requested.issubset(allowed):
        raise ValueError(f"scope exceeds client allowlist: {sorted(requested - allowed)}")
    return " ".join(sorted(requested))
