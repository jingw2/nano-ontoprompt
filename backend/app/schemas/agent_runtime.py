"""Agent runtime API schemas (P3A-TURNAPI).

Session/Turn command envelopes, status responses and stream-ticket receipts.
Turn creation returns 202 only after the authoritative state plus the
transactional dispatch outbox row are committed; the same idempotency key
replays the stable Turn without duplicating messages/Turns/dispatch rows.
"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    title: Optional[str] = None


class SessionOut(BaseModel):
    model_config = {"protected_namespaces": ()}
    id: str
    agent_id: str
    owner_user_id: str
    status: str
    active_turn_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CreateTurnRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    turn_id: Optional[str] = None  # client-supplied idempotent turn id
    user_message: str = Field(..., min_length=1, max_length=100_000)


class TurnAcceptedResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    turn_id: str
    session_id: str
    status: str = "queued"
    dispatch_generation: int = 1
    correlation_id: str
    stream_ticket: Optional[str] = None
    stream_ticket_url: Optional[str] = None


class TurnStatusResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    turn_id: str
    session_id: str
    status: str
    dispatch_generation: int
    request_message_id: Optional[str] = None
    response_message_id: Optional[str] = None
    error_code: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CancelTurnRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    reason: Optional[str] = None


class StreamTicketResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    turn_id: str
    ticket: str
    expires_at: datetime
    stream_ticket_url: str


class AgentMessageOut(BaseModel):
    model_config = {"protected_namespaces": ()}
    id: str
    session_id: str
    turn_id: Optional[str] = None
    role: Literal["user", "assistant", "system", "tool"]
    ordinal: int
    content: Optional[str] = None
    created_at: Optional[datetime] = None
