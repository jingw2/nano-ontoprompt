"""FastAPI dependency for endpoints protected by an OAuth access token
(as opposed to `app.deps.get_current_user`'s interactive-session tokens)."""
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.deps import get_db
from app.services import oauth_clients
from app.services.auth_service import decode_token, get_user_by_id
from app.services.runtime.credentials import RuntimeContext, RuntimePrincipal

oauth_bearer = HTTPBearer(auto_error=False)

# The audience a Runtime context bridged from an MCP OAuth access token
# carries (Task 18) — distinct from `app.routers.v2.runtime`'s REST
# audience so the two are never confused, even though `evaluate_access`
# itself does not branch on audience.
MCP_RUNTIME_AUDIENCE = "ontexus-mcp"


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
        payload = decode_token(credentials.credentials, expected_token_use="oauth_access")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload.get("sub")
    token_client_id = payload.get("client_id")
    if not user_id or not token_client_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = get_user_by_id(db, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    client = oauth_clients.get_client(db, token_client_id)
    if client is None or not client.is_active:
        raise HTTPException(status_code=401, detail="Client revoked")
    return OAuthContext(user_id=user.id, client_id=client.id, scope=set(payload.get("scope", "").split()))


def get_runtime_context(
    ctx: OAuthContext = Depends(get_oauth_context),
    db: Session = Depends(get_db),
) -> RuntimeContext:
    """Bridge an already-verified MCP OAuth access token into a Runtime
    `RuntimeContext` (Task 13).

    `get_oauth_context` has already re-derived `ctx.client_id` (the calling
    Agent/service identity) and `ctx.user_id` (the delegated user) from the
    signed token and live, active database rows — exactly the two verified
    principals (RFC 8693's `act`/`sub`) a real delegated credential carries.
    No separate token-exchange round trip is required for the MCP
    transport: this dependency only re-derives `security_domain_id` (not
    carried on `OAuthContext`) from the same live user row, and every field
    it returns still comes from `ctx`/the database, never from the request
    body — MCP tool arguments never supply agent_id/user_id, exactly as
    `app.services.mcp_tools.call_runtime_tool` enforces on the other end.
    """
    user = get_user_by_id(db, ctx.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    principal = RuntimePrincipal(
        agent_id=ctx.client_id, user_id=ctx.user_id, security_domain_id=user.security_domain_id,
        audience=MCP_RUNTIME_AUDIENCE, scope=frozenset(ctx.scope),
        token_id=f"mcp:{ctx.client_id}:{ctx.user_id}",
    )
    return RuntimeContext(principal=principal, correlation_id=str(uuid.uuid4()))
