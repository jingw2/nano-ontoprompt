"""Release-aware Agent index Celery task (P4A-INDEX).

Consumes the transactional 0006 derived-index outbox into the release-aware
candidate collection.  Never writes authoritative Neo4j/Chroma results and
never reads a per-release collection.
"""
from app.tasks.celery_app import celery_app


@celery_app.task(bind=True, name="agent.index_consume")
def agent_index_consume(self):
    import app.models  # noqa: F401 — register all tables
    from app.database import SessionLocal
    from app.services.indexes.release_aware import consume_outbox

    db = SessionLocal()
    try:
        return consume_outbox(db)
    finally:
        db.close()
