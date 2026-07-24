from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.limiter import limiter
from app.schemas.auth import LoginRequest, TokenResponse, RegisterRequest, PasswordChangeRequest
from app.schemas.user import UserOut
from app.services.auth_service import authenticate_user, create_access_token, hash_password, verify_password
from app.models.user import User
import uuid

router = APIRouter()

@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token({"sub": user.id, "role": user.role})
    return {"data": {"access_token": token, "token_type": "bearer"}, "message": "ok"}

@router.post("/register", status_code=201)
@limiter.limit("5/minute")
def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter((User.username == body.username) | (User.email == body.email)).first():
        raise HTTPException(status_code=409, detail="Username or email already exists")
    user = User(
        id=str(uuid.uuid4()),
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        role="viewer",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"data": UserOut.model_validate(user).model_dump(), "message": "ok"}

@router.get("/profile")
def profile(current_user: User = Depends(get_current_user)):
    return {"data": UserOut.model_validate(current_user).model_dump(), "message": "ok"}

@router.put("/password")
def change_password(body: PasswordChangeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password incorrect")
    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"message": "Password updated"}
