from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime


class AgentCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    default_model_config_version_id: str
    default_model_name: str
    system_prompt: Optional[str] = None
    memory_settings: dict = {}
    application_state_schema_version_id: Optional[str] = None  # default: built-in chat-v1


class AgentBasicVersionRequest(BaseModel):
    base_version_no: int
    name: str
    description: Optional[str] = None
    default_model_config_version_id: str
    default_model_name: str
    system_prompt: Optional[str] = None
    memory_settings: dict = {}
    application_state_schema_version_id: Optional[str] = None
    change_note: Optional[str] = None


class AgentVersionOut(BaseModel):
    id: str
    version_no: int
    name: str
    description: Optional[str] = None
    config_hash: str
    created_at: Optional[datetime] = None


class AgentOut(BaseModel):
    agent_id: str
    status: str
    visibility: str
    name: Optional[str] = None
    version_no: Optional[int] = None
    config_hash: Optional[str] = None
    versions_count: int = 0
