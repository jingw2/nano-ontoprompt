from typing import Dict, List, Optional
from pydantic import BaseModel


class CreateProviderRequest(BaseModel):
    name: str
    kind: str


class CreateConnectionRequest(BaseModel):
    provider_id: str


class CreateConnectionVersionRequest(BaseModel):
    connection_id: str
    endpoint: Optional[str] = None
    audience: Optional[str] = None
    scopes: Optional[List[str]] = None
    credential_reference: Optional[str] = None
    allowlists: Optional[Dict] = None


class ActivateConnectionVersionRequest(BaseModel):
    connection_id: str
    version_id: str


class PinMcpSchemaRequest(BaseModel):
    pass


class IssueMcpTokenRequest(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    expires_in_seconds: int
    scope: List[str] = []
    audience: Optional[str] = None
