"""Security-domain administration routes (registered by I-BACKEND under
`/api/v1`).  Deactivation revokes every refresh family in the domain without
deleting the domain or any refresh evidence.
"""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.user import User
from app.schemas.security_domain import (
    SecurityDomainDeactivateResponse,
    SecurityDomainResponse,
)
from app.services.user_security import UserSecurityError, deactivate_domain

router = APIRouter()


@router.get("/security-domains")
def list_security_domains(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    rows = db.execute(
        sa.text("SELECT id, key, status, created_at FROM security_domains ORDER BY key")
    ).mappings().all()
    return {"data": [dict(row) for row in rows], "message": "ok"}


@router.post("/security-domains/{domain_id}/deactivate")
def deactivate_security_domain(
    domain_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        receipt = deactivate_domain(db, domain_id, actor_id=current_user.id)
    except UserSecurityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"data": receipt, "message": "ok"}
