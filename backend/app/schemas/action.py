from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

class ActionCreate(BaseModel):
    name_cn: str
    name_en: Optional[str] = None
    description: Optional[str] = None
    parameters: List[dict] = []
    rules: List[dict] = []
    submission_criteria: List[str] = []
    side_effects: List[dict] = []
    linked_entities: List[str] = []
    linked_logic_ids: List[str] = []
    confidence: Optional[float] = None

class ActionUpdate(BaseModel):
    name_cn: Optional[str] = None
    name_en: Optional[str] = None
    description: Optional[str] = None
    parameters: Optional[List[dict]] = None
    rules: Optional[List[dict]] = None
    submission_criteria: Optional[List[str]] = None
    side_effects: Optional[List[dict]] = None
    linked_entities: Optional[List[str]] = None
    linked_logic_ids: Optional[List[str]] = None
    confidence: Optional[float] = None

class ActionOut(BaseModel):
    id: str
    ontology_id: str
    name_cn: str
    name_en: Optional[str]
    description: Optional[str]
    parameters: List[dict]
    rules: List[dict]
    submission_criteria: List[str]
    side_effects: List[dict]
    linked_entities: List[str]
    linked_logic_ids: List[str]
    confidence: float
    version: str
    status: Optional[str] = None
    enabled: Optional[bool] = None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
