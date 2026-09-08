"""Periodic short-term memory summary sweep (P6B-1).

Mirrors the existing agent-dispatch-publish beat pattern
(backend/app/tasks/celery_app.py) rather than a synchronous call from the
Turn critical path — summary regeneration can lag by one sweep interval
without user-facing impact, and a per-session failure here must never
propagate to fail an unrelated session's regeneration, let alone a Turn.
"""
import logging

from sqlalchemy import text

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def sweep_memory_summaries(db=None) -> dict:
    """Process every active session; `db` is injectable for tests, otherwise
    a fresh worker session is opened and closed here."""
    from app.services.memory.summary import maybe_regenerate_summary

    owns_session = db is None
    if owns_session:
        from app.database import SessionLocal
        db = SessionLocal()
    try:
        session_ids = [row[0] for row in db.execute(text(
            "SELECT id FROM agent_sessions WHERE status = 'active'"
        )).all()]
        regenerated = 0
        errors = 0
        for session_id in session_ids:
            try:
                if maybe_regenerate_summary(db, session_id=session_id):
                    regenerated += 1
            except Exception:
                errors += 1
                logger.exception("memory summary sweep failed for session %s", session_id)
                db.rollback()
        return {"processed": len(session_ids), "regenerated": regenerated, "errors": errors}
    finally:
        if owns_session:
            db.close()


@celery_app.task(name="agent.memory_summary_sweep")
def memory_summary_sweep_task():
    return sweep_memory_summaries()
