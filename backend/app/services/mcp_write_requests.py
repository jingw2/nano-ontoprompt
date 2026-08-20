"""MCP-native write-approval requests (P7E plan 2).

Deliberately decoupled from agent_tool_executions/agent_approvals, which are
hard NOT-NULL-FK'd to agent_turns — an MCP tool call is not a Turn. Reuses
preview_action (already Turn-agnostic) for the preview/hash computation and
mirrors ToolGateway._recheck_data_grant's exact write-capability check
(ontology_data_grants, capability "execute_instance_action"). Approval never
applies a real mutation — execute_approved_action's effect application is a
documented no-op for every existing caller too; this mirrors that same
system-wide limitation rather than pretending otherwise.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models.mcp_write_request import McpWriteRequest
from app.services.actions.preview import PreviewError, preview_action


class McpWriteRequestError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _has_write_grant(db: Session, ontology_id: str, user_id: str) -> bool:
    return db.execute(text(
        "SELECT 1 FROM ontology_data_grants WHERE ontology_id = :o AND user_id = :u "
        "AND status = 'active' AND capabilities::text LIKE :cap LIMIT 1"
    ), {"o": ontology_id, "u": user_id, "cap": '%"execute_instance_action"%'}).scalar_one_or_none() is not None


def create_write_request(
    db: Session, *, oauth_client_id: str, user_id: str, ontology_id: str, release_id: str,
    descriptor_id: str, parameters: dict, target_instance_id: str | None = None,
) -> dict:
    if not _has_write_grant(db, ontology_id, user_id):
        raise McpWriteRequestError("DATA_GRANT_DENIED")
    try:
        preview = preview_action(
            db, actor_id=user_id, agent_id=oauth_client_id, ontology_id=ontology_id,
            release_id=release_id, descriptor_id=descriptor_id, parameters=parameters,
            target_instance_id=target_instance_id,
        )
    except PreviewError as exc:
        raise McpWriteRequestError(str(exc))
    request_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    db.add(McpWriteRequest(
        id=request_id, oauth_client_id=oauth_client_id, user_id=user_id, ontology_id=ontology_id,
        release_id=release_id, descriptor_id=descriptor_id, target_instance_id=target_instance_id,
        parameters=parameters, preview_hash=preview["hash"], preview_canonical=preview["canonical"],
        status="pending", expires_at=now + timedelta(hours=settings.mcp_write_request_expire_hours),
    ))
    db.commit()
    return {"request_id": request_id, "status": "pending", "preview_hash": preview["hash"]}


def _row_status(row: McpWriteRequest) -> str:
    if row.status == "pending" and row.expires_at < datetime.now(timezone.utc):
        return "expired"
    return row.status


def _serialize(row: McpWriteRequest) -> dict:
    return {
        "id": row.id, "ontology_id": row.ontology_id, "release_id": row.release_id,
        "descriptor_id": row.descriptor_id, "target_instance_id": row.target_instance_id,
        "parameters": row.parameters, "preview_hash": row.preview_hash,
        "preview_canonical": row.preview_canonical, "status": _row_status(row),
        "created_at": row.created_at, "resolved_at": row.resolved_at,
    }


def get_write_request(db: Session, *, request_id: str, user_id: str) -> dict | None:
    row = db.execute(
        select(McpWriteRequest).where(McpWriteRequest.id == request_id, McpWriteRequest.user_id == user_id)
    ).scalar_one_or_none()
    return None if row is None else _serialize(row)


def list_pending_for_user(db: Session, *, user_id: str) -> list[dict]:
    rows = db.execute(
        select(McpWriteRequest)
        .where(McpWriteRequest.user_id == user_id, McpWriteRequest.status == "pending")
        .order_by(McpWriteRequest.created_at.desc())
    ).scalars().all()
    return [_serialize(r) for r in rows]


def _resolve(db: Session, *, request_id: str, actor_id: str, decision: str) -> dict:
    resolved_id = db.execute(
        update(McpWriteRequest)
        .where(McpWriteRequest.id == request_id, McpWriteRequest.user_id == actor_id, McpWriteRequest.status == "pending")
        .values(status=decision, resolved_at=datetime.now(timezone.utc), resolved_by=actor_id)
        .returning(McpWriteRequest.id)
    ).scalar_one_or_none()
    if resolved_id is None:
        db.rollback()
        raise McpWriteRequestError("NOT_FOUND_OR_ALREADY_RESOLVED")
    db.commit()
    return {"id": resolved_id, "status": decision}


def approve_write_request(db: Session, *, request_id: str, actor_id: str) -> dict:
    return _resolve(db, request_id=request_id, actor_id=actor_id, decision="approved")


def reject_write_request(db: Session, *, request_id: str, actor_id: str) -> dict:
    return _resolve(db, request_id=request_id, actor_id=actor_id, decision="rejected")
