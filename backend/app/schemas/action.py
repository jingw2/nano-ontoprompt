from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

class ActionCreate(BaseModel):
    name_cn: str
    name_en: Optional[str] = None
    description: Optional[str] = None
    execution_rule: Optional[str] = None
    function_code: Optional[str] = None
    linked_entities: List[str] = []
    linked_logic_ids: List[str] = []
    confidence: Optional[float] = None

class ActionUpdate(BaseModel):
    name_cn: Optional[str] = None
    name_en: Optional[str] = None
    description: Optional[str] = None
    execution_rule: Optional[str] = None
    function_code: Optional[str] = None
    linked_entities: Optional[List[str]] = None
    linked_logic_ids: Optional[List[str]] = None
    confidence: Optional[float] = None

class ActionOut(BaseModel):
    id: str
    ontology_id: str
    name_cn: str
    name_en: Optional[str]
    description: Optional[str]
    execution_rule: Optional[str]
    function_code: Optional[str]
    linked_entities: List[str]
    linked_logic_ids: List[str]
    confidence: float
    version: str
    status: Optional[str] = None
    enabled: Optional[bool] = None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
