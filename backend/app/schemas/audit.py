from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any, List, Literal

class AuditRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    model_id: str
    model_name: str

class AuditFinding(BaseModel):
    severity: Literal["critical", "warning", "info"]
    category: Literal[
        "isolated_entity", "broken_ref", "missing_relation",
        "low_coverage", "action_unreachable", "other"
    ]
    title: str
    description: str
    affected_items: List[str]

class AuditTaskOut(BaseModel):
    model_config = {"from_attributes": True, "protected_namespaces": ()}

    id: str
    ontology_id: str
    model_id: Optional[str]
    model_name: str
    status: str
    progress: Dict[str, Any]
    error: Optional[str]
    findings: Optional[List[Dict[str, Any]]]
    react_trace: Optional[List[Dict[str, Any]]]
    created_at: datetime
    updated_at: datetime
