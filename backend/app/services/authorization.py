"""Centralized application role mapping (P2A-RBAC).

The closed role set is `viewer|editor|admin` with an exact ceiling ordering.
Legacy role values (`user`, unknown, empty) always map to the `viewer` ceiling
so a stale pre-0004 role can never become a permission bypass.  The runtime
normalization hook keeps such values effective as viewer without touching the
additive 0004 backfill contract.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

ROLES = ("viewer", "editor", "admin")
ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}


def normalize_role(role: str | None) -> str:
    """Legacy `user` and any unknown value map to the viewer ceiling."""
    if role in ROLES:
        return role
    return "viewer"


def role_allows(role: str | None, required: str) -> bool:
    """True when the (possibly legacy) role satisfies the required ceiling."""
    return ROLE_RANK[normalize_role(role)] >= ROLE_RANK[normalize_role(required)]


def normalize_legacy_roles(db: Session) -> int:
    """Runtime migration hook: normalize every non-closed role value to viewer.

    Idempotent; returns the number of rows updated.  The additive 0004
    backfill already did this for the pre-0004 schema; this hook defends
    against any stragglers introduced by older binaries or direct writes.
    """
    result = db.execute(text(
        "UPDATE users SET role = 'viewer' WHERE role NOT IN ('viewer', 'editor', 'admin')"
    ))
    db.commit()
    return result.rowcount or 0
