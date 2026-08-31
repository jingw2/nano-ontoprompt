"""FastAPI dependency for Runtime endpoints protected by a trusted delegated
credential (as opposed to `app.deps.oauth`'s single-principal OAuth access
token). See `app.services.runtime.credentials` for the verification path."""
from fastapi.security import HTTPBearer

from app.services.runtime.credentials import RuntimeContext, RuntimePrincipal

runtime_bearer = HTTPBearer(auto_error=False)

# Denial reasons that mean "no usable credential was presented" (401);
# everything else is a policy denial against a credential that did verify
# structurally (403).
_UNAUTHENTICATED_REASONS = frozenset({
    "MISSING_DELEGATION", "INVALID_DELEGATION", "EXPIRED_DELEGATION", "REVOKED_DELEGATION",
})


def authorize_request(
    context: RuntimeContext, *, body_agent_id: str | None = None, body_user_id: str | None = None,
) -> RuntimePrincipal:
    """The verified delegated credential's principal is always authoritative
    — a request body's `agent_id`/`user_id` fields (if present at all) carry
    no authority and are never consulted. This function exists to make that
    contract explicit and testable at a single call site."""
    return context.principal
