"""Agent Turn dispatch Celery task (P3A-DISPATCH).

Claims a queued Turn with one CAS and extends its lease by heartbeat; the
pinned runtime worker (P4A-WORKER) consumes the fenced claim.  This module
never invokes LangGraph, model or tool code.
"""
from app.tasks.celery_app import celery_app


@celery_app.task(bind=True, name="agent.dispatch_claim")
def agent_dispatch_claim(self, turn_id: str, dispatch_generation: int,
                         worker_artifact_id: str, claim_token: str):
    import app.models  # noqa: F401 — register all tables
    from app.database import SessionLocal
    from app.services.runtime.dispatch import claim_turn

    db = SessionLocal()
    try:
        return claim_turn(
            db, turn_id=turn_id, dispatch_generation=dispatch_generation,
            worker_artifact_id=worker_artifact_id, claim_token=claim_token,
        )
    finally:
        db.close()


@celery_app.task(bind=True, name="agent.dispatch_heartbeat")
def agent_dispatch_heartbeat(self, turn_id: str, claim_token: str):
    import app.models  # noqa: F401
    from app.database import SessionLocal
    from app.services.runtime.dispatch import heartbeat_turn

    db = SessionLocal()
    try:
        return heartbeat_turn(db, turn_id=turn_id, claim_token=claim_token)
    finally:
        db.close()


@celery_app.task(bind=True, name="agent.dispatch_publish")
def agent_dispatch_publish(self, batch: int = 50):
    """Transactional-outbox publisher (P3A-DISPATCH): drain pending dispatch
    rows and enqueue the pinned runtime worker (`agent.turn_execute`) for
    each.  The broker hook runs inside the publisher transaction; when the
    broker is unavailable the publish raises and the rows stay `pending` for
    the next beat sweep (queue-unavailable path preserved)."""
    import app.models  # noqa: F401
    import uuid
    from sqlalchemy import text
    from app.database import SessionLocal
    from app.services.runtime.dispatch import publish_pending_dispatch

    db = SessionLocal()
    try:
        def _enqueue(outbox_id: str, turn_id: str, operation: str) -> str:
            # the claim CAS consumes the outbox row by exact generation, so
            # resolve it here and mint a fresh claim identity for the worker
            generation = db.execute(text(
                "SELECT dispatch_generation FROM agent_turn_dispatch_outbox "
                "WHERE id = :id"
            ), {"id": outbox_id}).scalar_one_or_none()
            if generation is None:
                return ""
            worker_artifact_id = f"publish:{outbox_id[:8]}"
            claim_token = str(uuid.uuid4())
            result = celery_app.send_task(
                "agent.turn_execute",
                args=[turn_id, generation, worker_artifact_id, claim_token],
            )
            return result.id or ""

        return publish_pending_dispatch(db, batch=batch, publish=_enqueue)
    finally:
        db.close()


@celery_app.task(bind=True, name="agent.dispatch_watchdog")
def agent_dispatch_watchdog(self):
    import app.models  # noqa: F401
    from app.database import SessionLocal
    from app.services.runtime.dispatch import watchdog_recover_delivered

    db = SessionLocal()
    try:
        return watchdog_recover_delivered(db)
    finally:
        db.close()


@celery_app.task(bind=True, name="agent.dispatch_sweeper")
def agent_dispatch_sweeper(self):
    import app.models  # noqa: F401
    from app.database import SessionLocal
    from app.services.runtime.dispatch import sweep_expired_leases

    db = SessionLocal()
    try:
        return sweep_expired_leases(db)
    finally:
        db.close()
