from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime


class CreateDataGrantRequest(BaseModel):
    user_id: str
    ontology_id: str
    capabilities: List[str]
    entity_allowlist: Optional[List[str]] = None
    property_allowlist: Optional[List[str]] = None
    relation_allowlist: Optional[List[str]] = None
    action_allowlist: Optional[List[str]] = None
    policy_version: str = "restricted-policy-dsl-v1"
    row_policy: Optional[dict] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None


class ReviseDataGrantRequest(BaseModel):
    base_revision: int
    capabilities: Optional[List[str]] = None
    row_policy: Optional[dict] = None
    valid_until: Optional[datetime] = None


class RevokeDataGrantRequest(BaseModel):
    base_revision: int
    reason: str
