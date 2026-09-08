from pydantic import BaseModel


class OAuthClientCreateRequest(BaseModel):
    client_name: str
    redirect_uris: list[str]
    allowed_scopes: list[str]


class OAuthClientOut(BaseModel):
    id: str
    client_name: str
    redirect_uris: list[str]
    allowed_scopes: list[str]
    is_active: bool


class OAuthClientPublicOut(BaseModel):
    client_id: str
    client_name: str


class OAuthConsentRequest(BaseModel):
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: str | None = None
    state: str | None = None
    decision: str  # "allow" | "deny"


class OAuthConsentResponse(BaseModel):
    redirect_uri: str
