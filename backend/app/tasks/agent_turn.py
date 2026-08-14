"""Read-only Agent Turn worker (P4A-WORKER).

Claims a queued Turn with the dispatch service's single CAS, runs the fixed
read-only graph through the LangGraph adapter, and persists runtime events.
On ambiguity it creates a clarification interrupt (resume later).  Only the
matching artifact worker commits; no API execution fallback and no Action
writes.
"""
import asyncio

from sqlalchemy import text

from app.tasks.celery_app import celery_app


@celery_app.task(bind=True, name="agent.turn_execute")
def agent_turn_execute(self, turn_id: str, dispatch_generation: int,
                       worker_artifact_id: str, claim_token: str):
    import app.models  # noqa: F401 — register all tables
    from app.database import SessionLocal
    from app.runtime.langgraph_adapter import LangGraphRuntimeAdapter, assemble_turn_context
    from app.services.runtime.dispatch import claim_turn

    db = SessionLocal()
    try:
        claim_turn(
            db, turn_id=turn_id, dispatch_generation=dispatch_generation,
            worker_artifact_id=worker_artifact_id, claim_token=claim_token,
        )
        row = db.execute(text(
            "SELECT t.session_id, s.agent_id, a.active_version_id, "
            "v.default_model_config_version_id, v.default_model_name "
            "FROM agent_turns t "
            "JOIN agent_sessions s ON s.id = t.session_id "
            "JOIN agents a ON a.id = s.agent_id "
            "JOIN agent_versions v ON v.id = a.active_version_id "
            "WHERE t.id = :id"
        ), {"id": turn_id}).mappings().one()
        context = assemble_turn_context(
            turn_id=turn_id, session_id=row["session_id"], agent_id=row["agent_id"],
            agent_version_id=row["active_version_id"], user_message="",
            model_config_version_id=row["default_model_config_version_id"],
            model_name=row["default_model_name"], runtime_artifact_id=worker_artifact_id,
        )
        adapter = LangGraphRuntimeAdapter()
        events = asyncio.run(adapter.start(context))
        return {"turn_id": turn_id, "events": [e.event_type for e in events]}
    finally:
        db.close()
