from typing import List, Literal, Optional
from pydantic import BaseModel
from datetime import datetime


class OntologyBindingPatch(BaseModel):
    """One immutable ontology-binding child row: the bound ontology, the data
    capabilities the Agent may use, and the exposed tool descriptors enabled
    for it (query / Logic / Action descriptor ids).  `enabled_categories`
    (MCP/query/write/logic/action) is the category-level enablement — a
    present value switches the runtime to category-mode filtering; None keeps
    the legacy `selected_tools`-only filter."""
    ontology_id: str
    capabilities: List[str] = []
    allowlists: dict = {}
    selected_tools: List[str] = []
    enabled_categories: Optional[List[str]] = None


class AgentCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    default_model_config_version_id: str
    default_model_name: str
    system_prompt: Optional[str] = None
    memory_settings: dict = {}
    application_state_schema_version_id: Optional[str] = None  # default: built-in chat-v1
    ontology_bindings: List[OntologyBindingPatch] = []


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
    ontology_bindings: Optional[List[OntologyBindingPatch]] = None  # None = keep existing


class AgentVersionOut(BaseModel):
    id: str
    version_no: int
    name: str
    description: Optional[str] = None
    config_hash: str
    default_model_config_version_id: Optional[str] = None
    default_model_name: Optional[str] = None
    system_prompt: Optional[str] = None
    memory_settings: Optional[dict] = None
    application_state_schema_version_id: Optional[str] = None
    change_note: Optional[str] = None
    prompt_generation_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    ontology_bindings: Optional[List[OntologyBindingPatch]] = None


class AgentOut(BaseModel):
    agent_id: str
    status: str
    visibility: str
    name: Optional[str] = None
    version_no: Optional[int] = None
    config_hash: Optional[str] = None
    versions_count: int = 0
    created_at: Optional[datetime] = None
    can_edit: bool = False


class PromptGenerationRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    base_version_no: int
    model_config_version_id: str
    model_name: str
    input_text: str


class PromptGenerationDecisionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    decision: Literal["accepted", "rejected"]


class PromptGenerationOut(BaseModel):
    model_config = {"protected_namespaces": ()}
    id: str
    agent_id: str
    base_version_no: int
    model_config_version_id: str
    model_name: str
    input_hash: str
    output_hash: str
    output_text: Optional[str] = None
    status: str
    requester_id: str
    requested_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None


class ToolValidationRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    ontology_ids: List[str] = []


class ToolValidationResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    valid: bool
    blocked: List[str] = []
    capabilities: List[str] = []


class RestoreVersionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    change_note: Optional[str] = None


class AgentAccessGrantOut(BaseModel):
    model_config = {"protected_namespaces": ()}
    id: str
    agent_id: str
    user_id: str
    capabilities: List[str] = []
    revision: int = 1
    status: str = "active"
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CreateAgentAccessGrantRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    user_id: str
    capabilities: List[str]


class ReviseAgentAccessGrantRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    base_revision: int
    capabilities: List[str]


class RevokeAgentAccessGrantRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    base_revision: int


class BindExternalToolRequest(BaseModel):
    tool_connection_version_id: str
    alias: str
