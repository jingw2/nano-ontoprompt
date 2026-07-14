from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

class ModelConfigCreate(BaseModel):
    name: str
    config_type: str = "llm"
    provider: str  # llm: openai|anthropic|compatible; ocr: paddleocr|tesseract|external_api
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    models: List[str] = []
    options: dict = {}

class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    config_type: Optional[str] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    models: Optional[List[str]] = None
    options: Optional[dict] = None

class ModelConfigOut(BaseModel):
    id: str
    name: str
    config_type: str = "llm"
    provider: str
    api_base: Optional[str]
    models: List[str]
    options: dict = {}
    created_by: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
