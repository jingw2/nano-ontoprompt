from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError
from app.database import SessionLocal
from app.services.auth_service import decode_token, get_user_by_id
from app.models.user import User
from app.services.authorization import role_allows

bearer = HTTPBearer(auto_error=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=403, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials, expected_token_use=None)
        user = get_user_by_id(db, payload["sub"])
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not role_allows(current_user.role, "admin"):
        raise HTTPException(status_code=403, detail="Admin required")
    return current_user

def require_editor(current_user: User = Depends(get_current_user)) -> User:
    """编辑权限：admin 或 editor 角色（legacy 值按 viewer 处理）。"""
    if not role_allows(current_user.role, "editor"):
        raise HTTPException(status_code=403, detail="Editor role required")
    return current_user
