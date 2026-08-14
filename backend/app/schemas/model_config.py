from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional


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


class ModelContractEntry(BaseModel):
    provider_model_revision: str
    tokenizer_family: Optional[str] = None
    tokenizer_revision: Optional[str] = None
    verified_context_window_tokens: Optional[int] = None
    verified_maximum_output_tokens: Optional[int] = None
    provider_contract_revision: Optional[str] = None
    provider_contract_hash: Optional[str] = None


class ModelVersionCreate(BaseModel):
    base_version: Optional[int] = None
    changelog: Optional[str] = None
    api_base: Optional[str] = None
    options: Optional[dict] = None
    models: Optional[List[str]] = None
    credential_binding: Optional[str] = None
    model_contract: Optional[List[ModelContractEntry]] = None


class ModelActiveVersion(BaseModel):
    version_no: int
    behavior_hash: str
    conservative_input_limit: Optional[int] = None
    created_at: Optional[datetime] = None


class ModelConfigOut(BaseModel):
    id: str
    name: str
    config_type: str = "llm"
    provider: str
    api_base: Optional[str] = None
    models: List[str] = []
    options: dict = {}
    status: str = "active"
    versions_count: int = 0
    active_version: Optional[ModelActiveVersion] = None  # LLM only; OCR/other keep legacy shape
    created_by: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class ModelVersionOut(BaseModel):
    model_config = {"from_attributes": True, "protected_namespaces": ()}

    id: str
    version_no: int
    provider: str
    api_base: Optional[str] = None
    options: dict = {}
    behavior_hash: str
    model_contract: List[ModelContractEntry] = []
    conservative_input_limit: Optional[int] = None
    created_at: Optional[datetime] = None


class ModelCredentialOut(BaseModel):
    status: str
    secret_revision: int
    rotated_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class RemediateModelMigrationRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    base_revision: str
    provider: str
    api_base: Optional[str] = None
    options: dict = {}
    model_contract: List[ModelContractEntry]
    credential_binding: str


class ArchiveModelMigrationRequest(BaseModel):
    base_revision: str
    reason: str
