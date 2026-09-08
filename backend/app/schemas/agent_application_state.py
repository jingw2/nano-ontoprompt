"""Agent application-state API schemas (P3B-STATEAUDIT).

Schema-validated snapshot/patch with base_revision + base_hash CAS; the
response carries the canonical snapshot, its revision/hash, and the pinned
schema tuple.  No arbitrary JSON state is accepted.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


class ApplicationStateResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    session_id: str
    revision: int
    hash: str
    schema_version_id: str
    schema_revision: int
    schema_hash: str
    state: Dict[str, Any]
    created_at: Optional[datetime] = None


class PatchApplicationStateRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    base_revision: int
    base_hash: str
    patch: Dict[str, Any] = {}


class AuditEventResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    id: str
    security_domain_id: str
    sequence: int
    actor_user_id: Optional[str] = None
    operation: str
    decision: str
    outcome: str
    correlation_id: Optional[str] = None
    occurred_at: Optional[datetime] = None
