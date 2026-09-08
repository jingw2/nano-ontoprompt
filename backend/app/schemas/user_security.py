"""User security request/response schemas (F0-SECURITY)."""
from pydantic import BaseModel


class UserSecurityResponse(BaseModel):
    user_id: str
    is_active: bool
