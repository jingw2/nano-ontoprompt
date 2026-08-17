"""Memory-free fixed-policy retention (P3A-RETENTION, Section 7).

The ten idempotent steps purge core Agent data using a fenced purge job
(lease/cursor/generation) and a purge marker that the checkpoint saver's
`adelete_thread` requires.  Each step is independently durable: a crash
mid-step resumes from the cursor; a step never runs a known effect twice.
As of P6A, the steps honor the active `RetentionPolicyVersion`'s duration
for each class (floored at `TABLE_MINIMUMS` — see policy.py) and legal
holds (see holds.py); only memory/vector-store cleanup is still out of
scope.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.retention.policy import acquire_domain_lock, resolve_active_duration
from app.services.retention.holds import is_held

DELIVERED_OUTBOX_RETENTION_DAYS = 30
STREAM_TICKET_EXPIRY_SECONDS = 60


class RetentionError(Exception):
    """Rejected retention operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


TEN_STEPS = (
    "redact_payloads",
    "delete_expired_stream_tickets",
    "delete_resolved_clarifications",
    "redact_runtime_content",
    "delete_model_and_node_rows",
    "delete_checkpoint_rows",
    "delete_delivered_outbox",
    "delete_messages_turn_marker",
    "clear_session_pointer",
    "graph_index_cleanup",
)


def claim_purge_job(db: Session, *, security_domain_id: str, purge_class: str,
                    lease_seconds: int = 30) -> dict | None:
    """Claim the per-domain/per-class purge job with a lease; returns None if
    another worker holds a live lease."""
    now = _now()
    lease = now + timedelta(seconds=lease_seconds)
    result = db.execute(text(
        "UPDATE agent_purge_jobs SET claim_token = :token, lease_expires_at = :lease, "
        "heartbeat_at = :now, generation = generation + 1, updated_at = :now "
        "WHERE security_domain_id = :dom AND purge_class = :cls "
        "AND (lease_expires_at IS NULL OR lease_expires_at <= :now) "
        "RETURNING id, claim_token, generation"
    ), {"token": _new_id(), "lease": lease, "now": now, "dom": security_domain_id,
        "cls": purge_class}).mappings().one_or_none()
    if result is None:
        db.commit()
        return None
    db.commit()
    return dict(result)


def _fenced(db: Session, job_id: str, claim_token: str) -> None:
    """Every purge batch must hold the live fence."""
    ok = db.execute(text(
        "SELECT 1 FROM agent_purge_jobs WHERE id = :id AND claim_token = :token "
        "AND lease_expires_at > now()"
    ), {"id": job_id, "token": claim_token}).scalar_one_or_none()
    if not ok:
        raise RetentionError("RETENTION_FENCE_LOST")


def _marker(db: Session, turn_id: str, job_id: str, *, policy_version_id: str | None = None) -> None:
    db.execute(text(
        "INSERT INTO agent_purge_markers (id, turn_id, policy_version_id, fixed_policy_hash, job_id, generation, created_at) "
        "VALUES (:id, :turn, :policy_version, 'fixed-policy-v1', :job, 1, now()) "
        "ON CONFLICT DO NOTHING"
    ), {"id": _new_id(), "turn": turn_id, "policy_version": policy_version_id, "job": job_id})


def run_fixed_purge(db: Session, *, security_domain_id: str, batch_size: int = 50,
                    job_id: str | None = None, claim_token: str | None = None) -> dict:
    """Execute the ten idempotent steps for a claimed batch of terminal Turns,
    using each class's currently active retention duration (never below its
    table minimum — see app/services/retention/policy.py:TABLE_MINIMUMS).
    A hold on a turn or its session protects that turn's redaction/deletion
    across steps 1, 4, 5, 6 in addition to the final Turn/message delete in
    step 8. The whole batch runs inside the domain's advisory lock so a hold
    or policy committed mid-batch is either fully visible or not yet
    visible — never torn."""
    if job_id is None or claim_token is None:
        raise RetentionError("RETENTION_JOB_REQUIRED")
    acquire_domain_lock(db, security_domain_id)
    _fenced(db, job_id, claim_token)
    # Candidate tables (agent_turns, agent_sessions, etc.) have no
    # security_domain_id column to filter on, so a per-domain sweep can't be
    # scoped safely today; raise rather than silently cross domain boundaries.
    domain_count = db.execute(text("SELECT count(*) FROM security_domains")).scalar_one()
    if domain_count > 1:
        raise RetentionError("RETENTION_MULTI_DOMAIN_PURGE_UNSUPPORTED")
    now = _now()
    ledger: dict[str, int] = {}

    def cutoff(class_action: str) -> datetime:
        return now - timedelta(days=resolve_active_duration(db, security_domain_id, class_action))

    # 1. redact payloads of snapshots
    ledger["redact_payloads"] = db.execute(text(
        "UPDATE agent_application_state_snapshots SET canonical_bytes = ''::bytea "
        "WHERE created_at < :cutoff AND NOT EXISTS ("
        "  SELECT 1 FROM retention_holds h WHERE h.security_domain_id = :domain "
        "  AND h.released_at IS NULL AND h.scope_type = 'session' "
        "  AND h.scope_id = agent_application_state_snapshots.session_id)"
    ), {"cutoff": cutoff("application_state_snapshot.redact"), "domain": security_domain_id}).rowcount or 0

    # 2. delete expired stream tickets (tied to each ticket's own expiry, not policy-governed)
    ledger["delete_expired_stream_tickets"] = db.execute(text(
        "DELETE FROM agent_stream_tickets WHERE expires_at < :now"
    ), {"now": now}).rowcount or 0

    # 3. delete resolved clarifications
    ledger["delete_resolved_clarifications"] = db.execute(text(
        "DELETE FROM agent_clarification_requests WHERE status = 'answered' AND answered_at < :cutoff"
    ), {"cutoff": cutoff("clarification.delete")}).rowcount or 0

    # 4. redact runtime event/trace/message content while keeping audit hashes
    ledger["redact_runtime_content"] = db.execute(text(
        "UPDATE agent_runtime_events SET payload = '{}'::json WHERE created_at < :cutoff "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM retention_holds h WHERE h.security_domain_id = :domain "
        "  AND h.released_at IS NULL AND ("
        "    (h.scope_type = 'turn' AND h.scope_id = agent_runtime_events.turn_id) OR "
        "    (h.scope_type = 'session' AND h.scope_id = ("
        "      SELECT t.session_id FROM agent_turns t WHERE t.id = agent_runtime_events.turn_id))))"
    ), {"cutoff": cutoff("runtime_event.redact"), "domain": security_domain_id}).rowcount or 0
    ledger["redact_runtime_content"] += db.execute(text(
        "UPDATE agent_messages SET content = NULL WHERE created_at < :cutoff AND role != 'user' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM retention_holds h WHERE h.security_domain_id = :domain "
        "  AND h.released_at IS NULL AND ("
        "    (h.scope_type = 'turn' AND h.scope_id = agent_messages.turn_id) OR "
        "    (h.scope_type = 'session' AND h.scope_id = agent_messages.session_id)))"
    ), {"cutoff": cutoff("message.redact"), "domain": security_domain_id}).rowcount or 0

    # 5. delete model invocations and node executions
    ledger["delete_model_and_node_rows"] = db.execute(text(
        "DELETE FROM agent_model_invocations WHERE created_at < :cutoff "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM retention_holds h WHERE h.security_domain_id = :domain "
        "  AND h.released_at IS NULL AND ("
        "    (h.scope_type = 'turn' AND h.scope_id = agent_model_invocations.turn_id) OR "
        "    (h.scope_type = 'session' AND h.scope_id = ("
        "      SELECT t.session_id FROM agent_turns t WHERE t.id = agent_model_invocations.turn_id))))"
    ), {"cutoff": cutoff("model_invocation.delete"), "domain": security_domain_id}).rowcount or 0
    ledger["delete_model_and_node_rows"] += db.execute(text(
        "DELETE FROM agent_node_executions WHERE created_at < :cutoff "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM retention_holds h WHERE h.security_domain_id = :domain "
        "  AND h.released_at IS NULL AND ("
        "    (h.scope_type = 'turn' AND h.scope_id = agent_node_executions.turn_id) OR "
        "    (h.scope_type = 'session' AND h.scope_id = ("
        "      SELECT t.session_id FROM agent_turns t WHERE t.id = agent_node_executions.turn_id))))"
    ), {"cutoff": cutoff("node_execution.delete"), "domain": security_domain_id}).rowcount or 0

    # 6. mark/delete checkpoint writes + child checkpoints
    checkpoint_cutoff = cutoff("checkpoint.delete")
    ledger["delete_checkpoint_rows"] = db.execute(text(
        "DELETE FROM agent_turn_checkpoint_writes WHERE created_at < :cutoff "
        "AND consumed_child_checkpoint_id IS NOT NULL "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM retention_holds h WHERE h.security_domain_id = :domain "
        "  AND h.released_at IS NULL AND ("
        "    (h.scope_type = 'turn' AND h.scope_id = agent_turn_checkpoint_writes.turn_id) OR "
        "    (h.scope_type = 'session' AND h.scope_id = ("
        "      SELECT t.session_id FROM agent_turns t WHERE t.id = agent_turn_checkpoint_writes.turn_id))))"
    ), {"cutoff": checkpoint_cutoff, "domain": security_domain_id}).rowcount or 0
    ledger["delete_checkpoint_rows"] += db.execute(text(
        "DELETE FROM agent_turn_checkpoints WHERE created_at < :cutoff "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM retention_holds h WHERE h.security_domain_id = :domain "
        "  AND h.released_at IS NULL AND ("
        "    (h.scope_type = 'turn' AND h.scope_id = agent_turn_checkpoints.turn_id) OR "
        "    (h.scope_type = 'session' AND h.scope_id = ("
        "      SELECT t.session_id FROM agent_turns t WHERE t.id = agent_turn_checkpoints.turn_id))))"
    ), {"cutoff": checkpoint_cutoff, "domain": security_domain_id}).rowcount or 0

    # 7. delete delivered outbox rows
    ledger["delete_delivered_outbox"] = db.execute(text(
        "DELETE FROM agent_turn_dispatch_outbox WHERE state IN "
        "('delivered', 'resolved_superseded', 'resolved_terminal', 'resolved_cancelled') "
        "AND resolved_at < :cutoff"
    ), {"cutoff": cutoff("dispatch_outbox.delete")}).rowcount or 0

    # 8. delete eligible terminal Turns + their messages + purge marker — skip held Turns/sessions
    turn_cutoff = cutoff("turn.delete")
    candidate_turns = db.execute(text(
        "SELECT t.id, t.session_id FROM agent_turns t "
        "WHERE t.status IN ('succeeded','failed','cancelled') AND t.updated_at < :cutoff "
        "ORDER BY t.updated_at LIMIT :batch"
    ), {"cutoff": turn_cutoff, "batch": batch_size}).mappings().all()
    removed = 0
    active_policy_version = db.execute(text(
        "SELECT active_version_id FROM retention_policies WHERE security_domain_id = :domain"
    ), {"domain": security_domain_id}).scalar_one_or_none()
    for row in candidate_turns:
        turn_id, session_id = row["id"], row["session_id"]
        if is_held(db, security_domain_id, "turn", turn_id) or is_held(db, security_domain_id, "session", session_id):
            continue
        _marker(db, turn_id, job_id, policy_version_id=active_policy_version)
        db.execute(text("DELETE FROM agent_messages WHERE turn_id = :id"), {"id": turn_id})
        db.execute(text("DELETE FROM agent_purge_markers WHERE turn_id = :id"), {"id": turn_id})
        db.execute(text("DELETE FROM agent_turns WHERE id = :id"), {"id": turn_id})
        removed += 1
    ledger["delete_messages_turn_marker"] = removed

    # 9. clear session pointer + delete sessions with no turns/messages
    ledger["clear_session_pointer"] = db.execute(text(
        "UPDATE agent_sessions SET active_turn_id = NULL, updated_at = :now "
        "WHERE active_turn_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM agent_turns t WHERE t.session_id = agent_sessions.id)"
    ), {"now": now}).rowcount or 0
    ledger["clear_session_pointer"] += db.execute(text(
        "DELETE FROM agent_sessions WHERE status = 'closed' AND NOT EXISTS "
        "(SELECT 1 FROM agent_messages m WHERE m.session_id = agent_sessions.id)"
    )).rowcount or 0

    # 10. graph-index cleanup: consume outbox rows for purged ontology
    ledger["graph_index_cleanup"] = db.execute(text(
        "DELETE FROM agent_index_outbox WHERE state = 'applied' AND created_at < :cutoff"
    ), {"cutoff": cutoff("graph_index.delete")}).rowcount or 0

    db.commit()
    return {"ledger": ledger, "turns_removed": removed}
