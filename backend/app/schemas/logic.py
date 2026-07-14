from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional, List

class LogicRuleCreate(BaseModel):
    name_cn: str
    name_en: Optional[str] = None
    description: Optional[str] = None
    formula: Optional[str] = None
    confidence: Optional[float] = None
    linked_entities: Optional[List[str]] = None
    enabled: Optional[bool] = None
    status: Optional[str] = None

class LogicRuleUpdate(BaseModel):
    name_cn: Optional[str] = None
    name_en: Optional[str] = None
    description: Optional[str] = None
    formula: Optional[str] = None
    confidence: Optional[float] = None
    linked_entities: Optional[List[str]] = None
    enabled: Optional[bool] = None
    status: Optional[str] = None

class LogicRuleOut(BaseModel):
    id: str
    ontology_id: str
    name_cn: str
    name_en: Optional[str]
    description: Optional[str]
    formula: Optional[str]
    confidence: float
    version: str
    enabled: bool
    status: str
    linked_entities: List[str] = []
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("linked_entities", mode="before")
    @classmethod
    def parse_linked_entities(cls, v):
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except Exception:
                return []
        return []
