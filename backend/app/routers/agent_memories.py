"""Memory inspection/correction/deletion API (P6B-3, Section 12.1)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.user import User
from app.services.memory.inspection import (
    MemoryConflictError, MemoryConsentRequiredError, confirm_memory, correct_memory,
    delete_memory, get_memory, list_conflicts, list_memories, reject_memory, resolve_conflict,
)

router = APIRouter()


class ConfirmMemoryRequest(BaseModel):
    consent: bool


class CorrectMemoryRequest(BaseModel):
    display_text: str
    confidence: float | None = None


class ResolveConflictRequest(BaseModel):
    winning_memory_id: str


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (MemoryConsentRequiredError, MemoryConflictError)):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


@router.get("/agents/{agent_id}/memories")
def get_memories(agent_id: str, status: str | None = None, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    items = list_memories(db, user_id=current_user.id, agent_id=agent_id, status=status)
    return {"data": {"items": items}}


@router.get("/agents/{agent_id}/memories/conflicts")
def get_conflicts(agent_id: str, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    items = list_conflicts(db, user_id=current_user.id, agent_id=agent_id)
    return {"data": {"items": items}}


@router.get("/agents/{agent_id}/memories/{memory_id}")
def get_memory_detail(agent_id: str, memory_id: str, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    item = get_memory(db, user_id=current_user.id, memory_id=memory_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Not found")
    return {"data": item}


@router.post("/agents/{agent_id}/memories/{memory_id}/confirm")
def post_confirm_memory(agent_id: str, memory_id: str, body: ConfirmMemoryRequest,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    try:
        result = confirm_memory(db, user_id=current_user.id, memory_id=memory_id,
                                consent=body.consent)
    except (MemoryConsentRequiredError, MemoryConflictError) as exc:
        raise _map_error(exc)
    return {"data": result}


@router.post("/agents/{agent_id}/memories/{memory_id}/reject")
def post_reject_memory(agent_id: str, memory_id: str, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    reject_memory(db, user_id=current_user.id, memory_id=memory_id)
    return {"data": {"status": "deleted"}}


@router.post("/agents/{agent_id}/memories/{memory_id}/correct")
def post_correct_memory(agent_id: str, memory_id: str, body: CorrectMemoryRequest,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    try:
        result = correct_memory(db, user_id=current_user.id, memory_id=memory_id,
                                display_text=body.display_text, confidence=body.confidence)
    except MemoryConflictError as exc:
        raise _map_error(exc)
    if result is None:
        raise HTTPException(status_code=404, detail="Not found")
    return {"data": result}


@router.post("/agents/{agent_id}/memories/{memory_id}/delete")
def post_delete_memory(agent_id: str, memory_id: str, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    delete_memory(db, user_id=current_user.id, memory_id=memory_id)
    return {"data": {"status": "deleted"}}


@router.post("/agents/{agent_id}/memories/conflicts/{conflict_id}/resolve")
def post_resolve_conflict(agent_id: str, conflict_id: str, body: ResolveConflictRequest,
                          db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    try:
        result = resolve_conflict(db, user_id=current_user.id, conflict_id=conflict_id,
                                  winning_memory_id=body.winning_memory_id)
    except MemoryConflictError as exc:
        raise _map_error(exc)
    return {"data": result}
