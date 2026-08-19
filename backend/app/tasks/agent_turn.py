"""Agent Turn worker (P4A-WORKER).

Claims a queued Turn with the dispatch service's single CAS, runs the fixed
read-only graph through the LangGraph adapter (the REAL runtime: model +
governed tool calls), and — inside one fenced transaction under the live
claim — persists every runtime event (persisted-before-notify), records the
assistant response message on success, CASes the Turn to `succeeded` (or
`failed` with the persisted error code when the runtime terminalizes with a
`turn_failed` event), releases the session active pointer, and resolves the
dispatch outbox.  Only the matching artifact worker commits; a stale fence
fails closed and never finalizes.  No API execution fallback and no Action
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
    from app.runtime.langgraph_runtime import LangGraphRuntime
    from app.services.runtime.context import resolve_pinned_context
    from app.services.runtime.dispatch import claim_turn
    from app.services.runtime import events as events_service
    from app.services.runtime.finalize import (
        finalize_turn_failed,
        finalize_turn_succeeded,
        record_assistant_message,
    )

    db = SessionLocal()
    try:
        claim = claim_turn(
            db, turn_id=turn_id, dispatch_generation=dispatch_generation,
            worker_artifact_id=worker_artifact_id, claim_token=claim_token,
        )
        row = db.execute(text(
            "SELECT t.session_id, s.agent_id, s.owner_user_id, a.active_version_id, "
            "v.default_model_config_version_id, v.default_model_name, "
            "m.content AS user_message "
            "FROM agent_turns t "
            "JOIN agent_sessions s ON s.id = t.session_id "
            "JOIN agents a ON a.id = s.agent_id "
            "JOIN agent_versions v ON v.id = a.active_version_id "
            "LEFT JOIN agent_messages m ON m.id = t.request_message_id "
            "WHERE t.id = :id"
        ), {"id": turn_id}).mappings().one()
        # P2B-TOOLS: resolve the pinned context (release citation + ontology
        # bindings) so the Tool Gateway exposes only this Agent's selected
        # tools and the resolve_snapshot event carries the release citation.
        pinned = resolve_pinned_context(
            db, turn_id=turn_id, session_id=row["session_id"])
        context = assemble_turn_context(
            turn_id=turn_id, session_id=row["session_id"], agent_id=row["agent_id"],
            agent_version_id=row["active_version_id"],
            user_message=row["user_message"] or "",
            release_id=pinned.release_id,
            model_config_version_id=row["default_model_config_version_id"],
            model_name=row["default_model_name"], runtime_artifact_id=worker_artifact_id,
            ontology_bindings=[dict(b) for b in pinned.ontology_tool_selection],
            external_tool_bindings=[dict(b) for b in pinned.tool_bindings],
            skill_bindings=[dict(b) for b in pinned.skill_bindings],
            citations=list(pinned.citations),
        )
        # the runtime executes the model + governed tools for this user
        context.extra["user_id"] = row["owner_user_id"]
        runtime = LangGraphRuntime(db)
        adapter = LangGraphRuntimeAdapter(runtime=runtime)
        runtime_events = asyncio.run(adapter.start(context))

        # one fenced transaction: persist events -> assistant message -> terminal
        for event in runtime_events:
            events_service.append_event(
                db, turn_id=turn_id, event_type=event.event_type,
                payload=dict(event.payload), commit=False,
            )
        terminal = runtime_events[-1].event_type if runtime_events else "turn_failed"
        if terminal == "turn_failed":
            error_code = runtime_events[-1].payload.get("error_code") or "RUNTIME_EXECUTION_FAILED"
            finalize_turn_failed(
                db, turn_id=turn_id, session_id=row["session_id"],
                claim_generation=claim["claim_generation"], claim_token=claim_token,
                error_code=str(error_code),
            )
            db.commit()
            return {"turn_id": turn_id, "status": "failed", "error_code": error_code,
                    "events": [e.event_type for e in runtime_events]}

        if terminal in ("request_clarification", "approval_required"):
            # The Turn's own status transition (running -> awaiting_*) already
            # happened inside create_clarification/create_approval's own
            # transaction. finalize_turn_succeeded/finalize_turn_failed both
            # CAS `WHERE status='running'` and raise TurnFinalizeError on a
            # zero-row match — calling either here would crash this task.
            # Persist the events (already queued above) and stop; the Turn
            # stays parked until a human resolves it and re-enqueues via a
            # fresh dispatch_generation.
            db.commit()
            return {"turn_id": turn_id, "status": "interrupted",
                    "events": [e.event_type for e in runtime_events]}

        final_text = next(
            (str(e.payload.get("message", "")) for e in reversed(runtime_events)
             if e.payload.get("message")),
            None,
        )
        content = final_text or f"Turn completed with {len(runtime_events)} runtime events."
        message_id = record_assistant_message(
            db, session_id=row["session_id"], turn_id=turn_id, content=content)
        finalize_turn_succeeded(
            db, turn_id=turn_id, session_id=row["session_id"],
            claim_generation=claim["claim_generation"], claim_token=claim_token,
            response_message_id=message_id,
        )
        db.commit()
        return {"turn_id": turn_id, "status": "succeeded",
                "events": [e.event_type for e in runtime_events]}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
