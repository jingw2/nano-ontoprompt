"""Periodic Chroma vector-outbox consumer for long-term memory (P6B-2b).

Consumes agent_memory_vector_outbox rows written by P6B-2a's extraction and
consent-revocation paths. Claims rows via SELECT ... FOR UPDATE SKIP LOCKED
(mirrors app/services/indexes/release_aware.py::consume_outbox — the
concurrency-safe pattern P6B-2a's own extraction sweep did NOT use, flagged
as a residual risk in that plan's final review; this consumer closes that
gap for its own outbox rather than repeating it). Per-row error isolation:
a failed row's state is rolled back to 'pending' for retry on the next
sweep, and one row's failure never blocks its siblings in the same batch.
"""
import logging

from sqlalchemy import text

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def sweep_memory_vector_outbox(db=None, *, batch: int = BATCH_SIZE) -> dict:
    from app.services.memory import vector_store

    owns_session = db is None
    if owns_session:
        from app.database import SessionLocal
        db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT ov.id AS outbox_id, ov.memory_id, ov.event_type, m.security_domain_id, "
            "m.display_text "
            "FROM agent_memory_vector_outbox ov "
            "JOIN agent_memories m ON m.id = ov.memory_id "
            "WHERE ov.state = 'pending' ORDER BY ov.created_at LIMIT :batch "
            "FOR UPDATE OF ov SKIP LOCKED"
        ), {"batch": batch}).mappings().all()
        applied = 0
        errors = 0
        for row in rows:
            try:
                if row["event_type"] == "upsert":
                    ok = vector_store.upsert_memory_embedding(
                        row["memory_id"], row["security_domain_id"], row["display_text"])
                    if not ok:
                        raise RuntimeError("vector store upsert returned False")
                    db.execute(text(
                        "UPDATE agent_memories SET embedding_model_version = :v WHERE id = :id"
                    ), {"v": vector_store.MEMORY_EMBEDDING_MODEL_VERSION, "id": row["memory_id"]})
                else:
                    ok = vector_store.delete_memory_embedding(
                        row["memory_id"], row["security_domain_id"])
                    if not ok:
                        raise RuntimeError("vector store delete returned False")
                    db.execute(text(
                        "UPDATE agent_memories SET embedding_model_version = NULL WHERE id = :id"
                    ), {"id": row["memory_id"]})
                db.execute(text(
                    "UPDATE agent_memory_vector_outbox SET state = 'applied' WHERE id = :id"
                ), {"id": row["outbox_id"]})
                db.commit()
                applied += 1
            except Exception:
                errors += 1
                logger.exception("memory vector sweep failed for outbox row %s", row["outbox_id"])
                db.rollback()
        return {"processed": len(rows), "applied": applied, "errors": errors}
    finally:
        if owns_session:
            db.close()


@celery_app.task(name="agent.memory_vector_sweep")
def memory_vector_sweep_task():
    return sweep_memory_vector_outbox()
