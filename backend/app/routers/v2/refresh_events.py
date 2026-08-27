"""Managed webhook ingress for durable refresh events."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.v2.connection import Connection
from app.models.v2.refresh import RefreshSourceState
from app.services.v2.incremental.event_adapters import EventIngressError, ManagedWebhookAdapter
from app.services.v2.incremental.event_ingest import EventIngestService


router = APIRouter()


def _source_secret_ref(db: Session, source_id: str) -> str:
    connection = db.get(Connection, source_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="source not found")
    config = dict(connection.config or {})
    secret_ref = config.get("webhook_secret_ref") or config.get("webhook_secret")
    if not secret_ref:
        state = db.query(RefreshSourceState).filter(
            RefreshSourceState.source_id == source_id,
        ).order_by(RefreshSourceState.updated_at.desc()).first()
        secret_ref = (state.configuration or {}).get("webhook_secret_ref") if state is not None else None
    if not isinstance(secret_ref, str) or not secret_ref:
        raise HTTPException(status_code=503, detail="managed webhook secret is unavailable")
    return secret_ref


@router.post("/events/webhook/{source_id}")
async def receive_webhook(source_id: str, request: Request, db: Session = Depends(get_db)):
    """Verify raw signed bytes before parsing and enqueue only a durable ID."""
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", request.headers.get("X-Signature", ""))
    timestamp = request.headers.get("X-Webhook-Timestamp", request.headers.get("X-Timestamp", ""))
    try:
        envelope = ManagedWebhookAdapter().verify_and_normalize(
            body,
            source_id=source_id,
            signature=signature,
            timestamp=timestamp,
            secret_ref=_source_secret_ref(db, source_id),
            now=datetime.now(timezone.utc),
        )
        receipt = EventIngestService().accept(
            db, envelope, lease_owner=f"webhook:{source_id}", now=datetime.now(timezone.utc),
        )
    except EventIngressError as exc:
        status = 401 if exc.reason_code in {"INVALID_SIGNATURE", "EXPIRED_SIGNATURE", "REPLAY_DETECTED"} else 400
        raise HTTPException(status_code=status, detail=exc.reason_code) from exc

    # The inbox/run transaction is already durable.  Dispatch failures do not
    # erase it; the persisted received row remains replayable by run ID.
    if receipt.run_id and receipt.status in {"received", "duplicate"}:
        try:
            from app.tasks.v2.refresh_tasks import refresh_event_task

            refresh_event_task.delay(receipt.run_id)
        except Exception:
            pass
    return {
        "event_id": receipt.event_id,
        "run_id": receipt.run_id,
        "status": receipt.status,
    }


__all__ = ["router"]
