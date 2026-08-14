from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Literal, Optional

RoleName = Literal["viewer", "editor", "admin"]

class UserOut(BaseModel):
    id: str
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    role: RoleName = "viewer"

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    role: Optional[RoleName] = None
    is_active: Optional[bool] = None
