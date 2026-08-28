"""FastAPI dependency for Runtime endpoints protected by a trusted delegated
credential (as opposed to `app.deps.oauth`'s single-principal OAuth access
token). See `app.services.runtime.credentials` for the verification path."""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.deps import get_db
from app.services.runtime.credentials import (
    RuntimeAccessError,
    RuntimeContext,
    RuntimePrincipal,
    verify_delegated_credential,
)

runtime_bearer = HTTPBearer(auto_error=False)

# Denial reasons that mean "no usable credential was presented" (401);
# everything else is a policy denial against a credential that did verify
# structurally (403).
_UNAUTHENTICATED_REASONS = frozenset({
    "MISSING_DELEGATION", "INVALID_DELEGATION", "EXPIRED_DELEGATION", "REVOKED_DELEGATION",
})


def get_runtime_context(audience: str, required_scope: str):
    """Dependency factory: returns a FastAPI dependency that verifies the
    bearer token as a delegated runtime credential for `audience` and
    `required_scope`, raising the appropriate HTTP status on any of the
    RUNTIME_DENIAL_CODES."""

    def _dependency(
        credentials: HTTPAuthorizationCredentials = Depends(runtime_bearer),
        db: Session = Depends(get_db),
    ) -> RuntimeContext:
        token = credentials.credentials if credentials else None
        try:
            return verify_delegated_credential(
                db, token, audience=audience, required_scope=required_scope,
                now=datetime.now(timezone.utc),
            )
        except RuntimeAccessError as exc:
            status_code = 401 if exc.reason_code in _UNAUTHENTICATED_REASONS else 403
            raise HTTPException(status_code=status_code, detail=exc.reason_code) from exc

    return _dependency


def authorize_request(
    context: RuntimeContext, *, body_agent_id: str | None = None, body_user_id: str | None = None,
) -> RuntimePrincipal:
    """The verified delegated credential's principal is always authoritative
    — a request body's `agent_id`/`user_id` fields (if present at all) carry
    no authority and are never consulted. This function exists to make that
    contract explicit and testable at a single call site."""
    return context.principal
