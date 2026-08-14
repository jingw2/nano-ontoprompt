from typing import Optional
from pydantic import BaseModel


class CreateApplicationStateSchemaRequest(BaseModel):
    application_key: str
    json_schema: dict


class CreateSchemaVersionRequest(BaseModel):
    base_active_revision: int
    json_schema: dict


class ActivateApplicationStateSchemaRequest(BaseModel):
    base_active_revision: int
    target_revision: Optional[str] = None  # null -> archive
