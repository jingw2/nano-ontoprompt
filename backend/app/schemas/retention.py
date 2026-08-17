from typing import Dict, List, Optional
from pydantic import BaseModel


class CreateRetentionPolicyRequest(BaseModel):
    security_domain_id: str
    rules: Dict[str, int]


class ActivateRetentionPolicyRequest(BaseModel):
    security_domain_id: str
    base_epoch: int


class RetentionPolicyVersionOut(BaseModel):
    id: str
    policy_id: str
    version_no: int
    status: str
    rules: Optional[Dict[str, int]] = None


class RetentionPolicyActivationOut(BaseModel):
    policy_id: str
    active_version_id: str
    epoch: int


class CreateRetentionHoldRequest(BaseModel):
    security_domain_id: str
    scope_type: str
    scope_id: str
    reason: str


class ReleaseRetentionHoldRequest(BaseModel):
    security_domain_id: str


class RetentionHoldOut(BaseModel):
    id: str
    scope_type: str
    scope_id: str
    reason: Optional[str] = None
    released: Optional[bool] = None
