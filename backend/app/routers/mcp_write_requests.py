"""Human-facing (interactive-JWT) endpoints for reviewing MCP write requests.

Self-service only: a user may only see/act on requests where
mcp_write_requests.user_id == current_user.id (the human who authorized the
OAuth client that proposed the write) — no admin override, matching
agent_approvals.py's existence-hiding-404 ownership idiom.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.mcp_write_requests import McpWriteRequestOut
from app.services import mcp_write_requests
from app.services.mcp_write_requests import McpWriteRequestError

router = APIRouter()


@router.get("/mcp/write-requests")
def list_write_requests(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    items = mcp_write_requests.list_pending_for_user(db, user_id=current_user.id)
    return {"data": {"items": [McpWriteRequestOut(**item).model_dump() for item in items]}}


@router.get("/mcp/write-requests/{request_id}")
def get_write_request(request_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = mcp_write_requests.get_write_request(db, request_id=request_id, user_id=current_user.id)
    if item is None:
        raise HTTPException(status_code=404, detail="Not found")
    return {"data": McpWriteRequestOut(**item).model_dump()}


@router.post("/mcp/write-requests/{request_id}/approve")
def approve_write_request(request_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        result = mcp_write_requests.approve_write_request(db, request_id=request_id, actor_id=current_user.id)
    except McpWriteRequestError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"data": result}


@router.post("/mcp/write-requests/{request_id}/reject")
def reject_write_request(request_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        result = mcp_write_requests.reject_write_request(db, request_id=request_id, actor_id=current_user.id)
    except McpWriteRequestError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"data": result}
