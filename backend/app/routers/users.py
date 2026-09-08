from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, require_admin, get_current_user
from app.schemas.user import UserOut, UserCreate, UserUpdate
from app.schemas.user_security import UserSecurityResponse
from app.services.auth_service import hash_password
from app.services.user_security import UserSecurityError, deactivate_user, soft_delete_user
from app.models.user import User
import uuid

router = APIRouter()

@router.get("")
def list_users(db: Session = Depends(get_db), _=Depends(require_admin)):
    users = db.query(User).all()
    return {"data": [UserOut.model_validate(u).model_dump() for u in users], "message": "ok"}

@router.post("", status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), _=Depends(require_admin)):
    if db.query(User).filter((User.username == body.username) | (User.email == body.email)).first():
        raise HTTPException(status_code=409, detail="Username or email already exists")
    user = User(id=str(uuid.uuid4()), username=body.username, email=body.email,
                password_hash=hash_password(body.password), role=body.role)
    db.add(user); db.commit(); db.refresh(user)
    return {"data": UserOut.model_validate(user).model_dump(), "message": "ok"}

@router.get("/{user_id}")
def get_user(user_id: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"data": UserOut.model_validate(user).model_dump(), "message": "ok"}

@router.put("/{user_id}")
def update_user(user_id: str, body: UserUpdate, db: Session = Depends(get_db), _=Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(user, k, v)
    db.commit(); db.refresh(user)
    return {"data": UserOut.model_validate(user).model_dump(), "message": "ok"}

@router.post("/{user_id}/deactivate")
def deactivate_user_route(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        receipt = deactivate_user(db, user_id, actor_id=current_user.id)
    except UserSecurityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"data": UserSecurityResponse(**receipt).model_dump(), "message": "ok"}

@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    try:
        # legacy DELETE becomes an atomic soft deletion: revoke + deactivate, never delete
        soft_delete_user(db, user_id, actor_id=current_user.id)
    except UserSecurityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
