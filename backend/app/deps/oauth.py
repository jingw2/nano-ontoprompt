"""FastAPI dependency for endpoints protected by an OAuth access token
(as opposed to `app.deps.get_current_user`'s interactive-session tokens)."""
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.deps import get_db
from app.services import oauth_clients
from app.services.auth_service import decode_token, get_user_by_id

oauth_bearer = HTTPBearer(auto_error=False)


@dataclass
class OAuthContext:
    user_id: str
    client_id: str
    scope: set[str]


def get_oauth_context(
    credentials: HTTPAuthorizationCredentials = Depends(oauth_bearer),
    db: Session = Depends(get_db),
) -> OAuthContext:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("token_use") != "oauth_access":
        raise HTTPException(status_code=401, detail="Not an OAuth access token")
    user = get_user_by_id(db, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    client = oauth_clients.get_client(db, payload["client_id"])
    if client is None or not client.is_active:
        raise HTTPException(status_code=401, detail="Client revoked")
    return OAuthContext(user_id=user.id, client_id=client.id, scope=set(payload.get("scope", "").split()))
