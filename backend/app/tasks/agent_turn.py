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
import json
import threading
import uuid

from sqlalchemy import text

from app.config import settings
from app.tasks.celery_app import celery_app


_HEARTBEAT_INTERVAL_SECONDS = 10


def _resolve_business_journey(model_config_options) -> dict | None:
    """A turn's Agent is routed through the business-journey two-completion
    protocol (`LangGraphRuntime._run_business_journey_turn`) precisely when
    its PINNED, IMMUTABLE model configuration was one `evals.business_
    journeys.orchestrator.prepare_journey` created — there is no separate
    request field or browser signal for this (an ordinary human turn must
    behave identically to today); the association is carried entirely by
    that immutable config's own `options` JSON
    (`evals.business_journeys.api_client.JourneyApiClient.create_model_config`
    tags it there at creation time, the one and only place a business-
    journey model configuration is ever minted). `options` round-trips as a
    dict on PostgreSQL but can arrive as a JSON string on the SQLite unit
    harness — decode defensively, matching the same duality this codebase
    already normalizes for `EntityInstance.row_data` elsewhere.

    GATED unconditionally behind `settings.business_journey_acceptance_
    enabled` (default `False`): `model_config_versions.options` is an
    editor-writable, unvalidated JSON blob, and every value this function
    checks for (provider/model/origin constants) is public knowledge — an
    untrusted editor could otherwise tag their OWN model config and opt
    their OWN Agent's turns into this privileged mode (server
    `DEEPSEEK_API_KEY` credential instead of the config's own; a real,
    no-approval-gate `entity_instances` mutation via `execute_automatic_
    low_risk_action`). Real deployments never set this flag; `app.config`
    additionally refuses to start with it set under `ENVIRONMENT=production`."""
    if not settings.business_journey_acceptance_enabled:
        return None
    if isinstance(model_config_options, (str, bytes, bytearray)):
        try:
            model_config_options = json.loads(model_config_options) if model_config_options else {}
        except (TypeError, ValueError):
            return None
    if not isinstance(model_config_options, dict):
        return None
    candidate = model_config_options.get("business_journey")
    if not isinstance(candidate, dict):
        return None
    run_id = candidate.get("run_id")
    journey_id = candidate.get("journey_id")
    if not run_id or not journey_id:
        return None
    return {"run_id": str(run_id), "journey_id": str(journey_id)}


def _load_turn_dispatch_row(db, *, turn_id: str):
    """The one query a dispatched Turn's context is assembled from — pulled
    into its own function so the business-journey detection (`options ->
    model_config_options -> _resolve_business_journey`) is directly
    testable without needing the full claim/runtime/finalize machinery
    `agent_turn_execute` itself requires."""
    return db.execute(text(
        "SELECT t.session_id, s.agent_id, s.owner_user_id, a.active_version_id, "
        "v.default_model_config_version_id, v.default_model_name, mcv.options AS model_config_options, "
        "m.content AS user_message "
        "FROM agent_turns t "
        "JOIN agent_sessions s ON s.id = t.session_id "
        "JOIN agents a ON a.id = s.agent_id "
        "JOIN agent_versions v ON v.id = a.active_version_id "
        "LEFT JOIN model_config_versions mcv ON mcv.id = v.default_model_config_version_id "
        "LEFT JOIN agent_messages m ON m.id = t.request_message_id "
        "WHERE t.id = :id"
    ), {"id": turn_id}).mappings().one()


@celery_app.task(bind=True, name="agent.turn_execute")
def agent_turn_execute(self, turn_id: str, dispatch_generation: int,
                       worker_artifact_id: str, claim_token: str):
    import app.models  # noqa: F401 — register all tables
    from app.database import SessionLocal
    from app.runtime.langgraph_adapter import LangGraphRuntimeAdapter, assemble_turn_context
    from app.runtime.langgraph_runtime import LangGraphRuntime
    from app.services.runtime.context import resolve_pinned_context
    from app.services.runtime.dispatch import claim_turn, heartbeat_turn
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
        row = _load_turn_dispatch_row(db, turn_id=turn_id)
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
            business_journey=_resolve_business_journey(row["model_config_options"]),
        )
        # the runtime executes the model + governed tools for this user
        context.extra["user_id"] = row["owner_user_id"]
        context.extra["claim_token"] = claim_token
        runtime = LangGraphRuntime(db)
        adapter = LangGraphRuntimeAdapter(runtime=runtime)
        heartbeat_stop = threading.Event()
        heartbeat_errors = []

        def heartbeat_while_running():
            while not heartbeat_stop.wait(_HEARTBEAT_INTERVAL_SECONDS):
                try:
                    heartbeat_db = SessionLocal()
                    try:
                        heartbeat_turn(
                            heartbeat_db, turn_id=turn_id, claim_token=claim_token,
                        )
                    finally:
                        heartbeat_db.close()
                except Exception as exc:
                    heartbeat_errors.append(exc)
                    return

        heartbeat_thread = threading.Thread(
            target=heartbeat_while_running, name=f"turn-heartbeat-{turn_id}", daemon=True,
        )
        heartbeat_thread.start()
        try:
            runtime_events = asyncio.run(adapter.start(context))
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()
        if heartbeat_errors:
            raise heartbeat_errors[0]

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
        db.execute(text(
            "INSERT INTO agent_memory_extraction_outbox (id, turn_id, session_id, state, created_at) "
            "VALUES (:id, :turn_id, :session_id, 'pending', now())"
        ), {"id": str(uuid.uuid4()), "turn_id": turn_id, "session_id": row["session_id"]})
        db.commit()
        return {"turn_id": turn_id, "status": "succeeded",
                "events": [e.event_type for e in runtime_events]}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
