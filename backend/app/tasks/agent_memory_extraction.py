"""Periodic long-term memory extraction sweep (P6B-2a).

Consumes agent_memory_extraction_outbox rows written by agent_turn.py at
Turn finalization. Mirrors P6B-1's summary sweep pattern (best-effort,
per-row error isolation, never on the Turn critical path) but is event-
driven off an outbox rather than a periodic full-table scan, since
extraction is naturally a per-Turn event rather than a threshold condition.
"""
import logging

from sqlalchemy import text

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def sweep_memory_extraction(db=None) -> dict:
    from app.services.memory.extraction import extract_memories_for_turn

    owns_session = db is None
    if owns_session:
        from app.database import SessionLocal
        db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT id, turn_id FROM agent_memory_extraction_outbox "
            "WHERE state = 'pending' ORDER BY created_at LIMIT :limit"
        ), {"limit": BATCH_SIZE}).mappings().all()
        applied = 0
        errors = 0
        for row in rows:
            try:
                extract_memories_for_turn(db, turn_id=row["turn_id"])
                db.execute(text(
                    "UPDATE agent_memory_extraction_outbox SET state = 'applied', processed_at = now() "
                    "WHERE id = :id"
                ), {"id": row["id"]})
                db.commit()
                applied += 1
            except Exception:
                errors += 1
                logger.exception("memory extraction failed for turn %s", row["turn_id"])
                db.rollback()
        return {"processed": len(rows), "applied": applied, "errors": errors}
    finally:
        if owns_session:
            db.close()


@celery_app.task(name="agent.memory_extraction_sweep")
def memory_extraction_sweep_task():
    return sweep_memory_extraction()
