"""Security-domain request/response schemas (F0-SECURITY)."""
from datetime import datetime

from pydantic import BaseModel


class SecurityDomainResponse(BaseModel):
    id: str
    key: str
    status: str
    created_at: datetime | None = None


class SecurityDomainDeactivateResponse(BaseModel):
    domain_id: str
    revoked_families: int
