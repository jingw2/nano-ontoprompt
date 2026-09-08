"""Account and security-domain revocation.

User deactivation, the user DELETE-as-soft-delete transition, and security
-domain deactivation atomically revoke every active refresh family (via
`revoke_refresh_families`/domain-wide revocation) without ever physically
deleting User, family, or token rows; the append-only refresh evidence stays
queryable for audit.
"""
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.services.auth_refresh import revoke_refresh_families
from app.services.auth_service import get_user_by_id
from app.services.governance_audit import enqueue_audit


class UserSecurityError(Exception):
    pass


def _enqueue_audit(db: Session, *, security_domain_id: str, correlation_id: str,
                   operation: str, actor_user_id: str) -> None:
    enqueue_audit(
        db.connection(),
        security_domain_id=security_domain_id,
        correlation_id=correlation_id,
        operation=operation,
        decision="allow",
        outcome="succeeded",
        actor_user_id=actor_user_id,
        retention_class="standard",
    )


def deactivate_user(db: Session, user_id: str, actor_id: str) -> dict:
    user = get_user_by_id(db, user_id)
    if user is None:
        raise UserSecurityError("user not found")
    user.is_active = False
    revoke_refresh_families(db, user_id)
    _enqueue_audit(
        db, security_domain_id=user.security_domain_id,
        correlation_id=f"user-security:{user_id}",
        operation="user.deactivate", actor_user_id=actor_id,
    )
    db.commit()
    return {"user_id": user_id, "is_active": False}


def soft_delete_user(db: Session, user_id: str, actor_id: str) -> dict:
    """The legacy DELETE contract becomes an atomic soft deletion."""
    return deactivate_user(db, user_id, actor_id)


def revoke_families_in_domain(db: Session, security_domain_id: str) -> int:
    result = db.execute(
        sa.text(
            "UPDATE auth_refresh_families SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP "
            "WHERE security_domain_id = :domain AND status = 'active'"
        ),
        {"domain": security_domain_id},
    )
    return result.rowcount


def deactivate_domain(db: Session, security_domain_id: str, actor_id: str) -> dict:
    domain = db.execute(
        sa.text("SELECT id FROM security_domains WHERE id = :id"),
        {"id": security_domain_id},
    ).scalar_one_or_none()
    if domain is None:
        raise UserSecurityError("domain not found")
    revoked = revoke_families_in_domain(db, security_domain_id)
    _enqueue_audit(
        db, security_domain_id=security_domain_id,
        correlation_id=f"security-domain:{security_domain_id}",
        operation="security-domain.deactivate", actor_user_id=actor_id,
    )
    db.commit()
    return {"domain_id": security_domain_id, "revoked_families": revoked}
