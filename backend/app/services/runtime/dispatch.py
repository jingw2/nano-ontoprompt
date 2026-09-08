"""Durable Agent Turn dispatch (P3A-DISPATCH, Section 6).

Transactional outbox publisher, single CAS claim, heartbeat lease extension,
delivered-deadline watchdog, expired-lease sweeper and pre/post-claim cancel.
No LangGraph/model/tool call happens here: the Celery task claims the Turn and
hands the fenced claim to the runtime worker.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


class DispatchError(Exception):
    """Rejected dispatch operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


ACTIVE_TURN_STATUSES = (
    "queued", "running", "awaiting_approval", "awaiting_clarification",
    "reconciling", "cancelling",
)
TERMINAL_TURN_STATUSES = ("succeeded", "failed", "cancelled")


def publish_pending_dispatch(
    db: Session, *, batch: int = 50, deadline_seconds: int = 20,
    publish: callable | None = None,
) -> list[dict]:
    """Claim pending outbox rows with FOR UPDATE SKIP LOCKED and mark them
    delivered with a claim deadline.  `publish` is a broker hook
    (outbox_id, turn_id, operation) -> broker_message_id; a no-op when None
    (unit tests / queue-unavailable keep the row pending for retry)."""
    rows = db.execute(text(
        "SELECT o.id AS outbox_id, o.turn_id, o.dispatch_generation, o.operation "
        "FROM agent_turn_dispatch_outbox o "
        "WHERE o.state = 'pending' "
        "ORDER BY o.created_at "
        "LIMIT :batch FOR UPDATE SKIP LOCKED"
    ), {"batch": batch}).mappings().all()
    published = []
    now = _now()
    for row in rows:
        broker_message_id = publish(row["outbox_id"], row["turn_id"], row["operation"]) if publish else None
        db.execute(text(
            "UPDATE agent_turn_dispatch_outbox "
            "SET state = 'delivered', published_at = :now, broker_message_id = :bid, "
            "claim_deadline_at = :deadline "
            "WHERE id = :id AND state = 'pending'"
        ), {
            "now": now, "bid": broker_message_id, "id": row["outbox_id"],
            "deadline": now + timedelta(seconds=deadline_seconds),
        })
        published.append(dict(row) | {"broker_message_id": broker_message_id, "state": "delivered"})
    db.commit()
    return published


def claim_turn(
    db: Session, *, turn_id: str, dispatch_generation: int, worker_artifact_id: str,
    claim_token: str, lease_seconds: int = 30,
) -> dict:
    """One PostgreSQL CAS claim: status queued, exact generation, no
    cancellation, no unexpired lease, matching runtime tuple; atomically
    increments claim_generation and records the worker claim.  Zero affected
    rows means the claim is stale (another worker, cancel, or old generation)."""
    now = _now()
    lease_expires = now + timedelta(seconds=lease_seconds)
    result = db.execute(text(
        "UPDATE agent_turns "
        "SET status = 'running', claim_generation = :gen, claim_token = :token, "
        "worker_artifact_id = :artifact, lease_expires_at = :lease, heartbeat_at = :now, "
        "updated_at = :now "
        "WHERE id = :id AND status = 'queued' AND dispatch_generation = :dgen "
        "AND cancel_requested_at IS NULL "
        "AND (lease_expires_at IS NULL OR lease_expires_at <= :now) "
        "AND (worker_artifact_id IS NULL OR worker_artifact_id = :artifact)"
    ), {
        "id": turn_id, "gen": 1, "token": claim_token, "artifact": worker_artifact_id,
        "lease": lease_expires, "now": now, "dgen": dispatch_generation,
    })
    if result.rowcount != 1:
        db.rollback()
        raise DispatchError("DISPATCH_CLAIM_STALE")
    # atomically mark the matching outbox row claimed and its ancestors superseded
    db.execute(text(
        "UPDATE agent_turn_dispatch_outbox "
        "SET state = 'claimed', resolved_at = :now, resolution = 'claimed' "
        "WHERE turn_id = :id AND dispatch_generation = :dgen AND operation = 'turn' "
        "AND state = 'delivered'"
    ), {"id": turn_id, "dgen": dispatch_generation, "now": now})
    db.execute(text(
        "UPDATE agent_turn_dispatch_outbox "
        "SET state = 'resolved_superseded', resolved_at = :now, resolution = 'superseded' "
        "WHERE turn_id = :id AND dispatch_generation < :dgen AND state IN "
        "('pending', 'delivered', 'delivered_unclaimed')"
    ), {"id": turn_id, "dgen": dispatch_generation, "now": now})
    db.commit()
    return {
        "turn_id": turn_id, "claim_generation": 1, "claim_token": claim_token,
        "worker_artifact_id": worker_artifact_id, "lease_expires_at": lease_expires,
    }


def heartbeat_turn(db: Session, *, turn_id: str, claim_token: str, lease_seconds: int = 30) -> dict:
    """Extend the worker lease, fencing on the exact claim token."""
    now = _now()
    lease_expires = now + timedelta(seconds=lease_seconds)
    result = db.execute(text(
        "UPDATE agent_turns SET heartbeat_at = :now, lease_expires_at = :lease, updated_at = :now "
        "WHERE id = :id AND claim_token = :token AND status NOT IN "
        "('succeeded', 'failed', 'cancelled')"
    ), {"id": turn_id, "token": claim_token, "now": now, "lease": lease_expires})
    if result.rowcount != 1:
        db.rollback()
        raise DispatchError("TURN_FENCE_LOST")
    db.commit()
    return {"turn_id": turn_id, "lease_expires_at": lease_expires}


def watchdog_recover_delivered(db: Session, *, now: datetime | None = None) -> list[dict]:
    """Delivered outbox rows whose claim deadline passed with no claim for that
    exact generation: CAS the Turn generation, mark the old row
    delivered_unclaimed, and insert a unique recovery outbox."""
    now = now or _now()
    rows = db.execute(text(
        "SELECT o.id AS outbox_id, o.turn_id, o.dispatch_generation, o.operation "
        "FROM agent_turn_dispatch_outbox o "
        "WHERE o.state = 'delivered' AND o.claim_deadline_at < :now "
        "ORDER BY o.created_at FOR UPDATE SKIP LOCKED"
    ), {"now": now}).mappings().all()
    recovered = []
    for row in rows:
        # CAS the turn to a fresh generation; if the turn is already terminal,
        # resolve the row instead.
        terminal = db.execute(text(
            "SELECT status FROM agent_turns WHERE id = :id FOR UPDATE"
        ), {"id": row["turn_id"]}).mappings().one_or_none()
        if not terminal:
            db.rollback()
            raise DispatchError("TURN_NOT_FOUND")
        if terminal["status"] in TERMINAL_TURN_STATUSES:
            db.execute(text(
                "UPDATE agent_turn_dispatch_outbox SET state = 'resolved_terminal', "
                "resolved_at = :now, resolution = 'terminal' WHERE id = :id"
            ), {"id": row["outbox_id"], "now": now})
            db.commit()
            continue
        new_generation = db.execute(text(
            "UPDATE agent_turns SET dispatch_generation = dispatch_generation + 1, updated_at = :now "
            "WHERE id = :id AND dispatch_generation = :dgen RETURNING dispatch_generation"
        ), {"id": row["turn_id"], "dgen": row["dispatch_generation"], "now": now}).scalar_one_or_none()
        if new_generation is None:
            db.rollback()
            continue  # generation changed concurrently; next sweep handles it
        db.execute(text(
            "UPDATE agent_turn_dispatch_outbox SET state = 'delivered_unclaimed', "
            "resolved_at = :now, resolution = 'unclaimed' WHERE id = :id"
        ), {"id": row["outbox_id"], "now": now})
        recovery_id = _new_id()
        db.execute(text(
            "INSERT INTO agent_turn_dispatch_outbox "
            "(id, turn_id, dispatch_generation, operation, state, superseded_by_outbox_id, created_at) "
            "VALUES (:id, :turn, :gen, 'delivery_recovery', 'pending', :old_id, :now)"
        ), {"id": recovery_id, "turn": row["turn_id"], "gen": new_generation,
            "old_id": row["outbox_id"], "now": now})
        db.commit()
        recovered.append({"turn_id": row["turn_id"], "old_generation": row["dispatch_generation"],
                          "new_generation": new_generation, "recovery_outbox_id": recovery_id})
    return recovered


def sweep_expired_leases(db: Session, *, now: datetime | None = None) -> list[dict]:
    """Expired leases on running turns: bump the generation and insert a
    recovery outbox so a fresh claim can resume; the turn returns to queued
    without terminalizing (worker loss never sets terminal state)."""
    now = now or _now()
    rows = db.execute(text(
        "SELECT id FROM agent_turns "
        "WHERE status = 'running' AND lease_expires_at < :now AND cancel_requested_at IS NULL "
        "FOR UPDATE SKIP LOCKED"
    ), {"now": now}).mappings().all()
    swept = []
    for row in rows:
        new_generation = db.execute(text(
            "UPDATE agent_turns SET dispatch_generation = dispatch_generation + 1, "
            "status = 'queued', claim_token = NULL, worker_artifact_id = NULL, "
            "lease_expires_at = NULL, updated_at = :now "
            "WHERE id = :id RETURNING dispatch_generation"
        ), {"id": row["id"], "now": now}).scalar_one_or_none()
        if new_generation is None:
            db.rollback()
            continue
        recovery_id = _new_id()
        db.execute(text(
            "INSERT INTO agent_turn_dispatch_outbox "
            "(id, turn_id, dispatch_generation, operation, state, created_at) "
            "VALUES (:id, :turn, :gen, 'delivery_recovery', 'pending', :now)"
        ), {"id": recovery_id, "turn": row["id"], "gen": new_generation, "now": now})
        db.commit()
        swept.append({"turn_id": row["id"], "new_generation": new_generation})
    return swept


def cancel_turn(db: Session, *, turn_id: str, actor_id: str) -> dict:
    """Pre-claim cancel: queued with no live claim -> terminal cancelled,
    generation bumped to invalidate published messages, all unresolved outbox
    rows resolved_cancelled, session pointer cleared.  A claimed turn CASes
    running|awaiting_*|reconciling -> cancelling with the signal for the worker."""
    now = _now()
    turn = db.execute(text(
        "SELECT id, status, session_id, claim_token FROM agent_turns WHERE id = :id FOR UPDATE"
    ), {"id": turn_id}).mappings().one_or_none()
    if not turn:
        db.rollback()
        raise DispatchError("TURN_NOT_FOUND")
    if turn["status"] == "queued" and turn["claim_token"] is None:
        new_generation = db.execute(text(
            "UPDATE agent_turns SET status = 'cancelled', cancel_requested_at = :now, "
            "dispatch_generation = dispatch_generation + 1, updated_at = :now "
            "WHERE id = :id RETURNING dispatch_generation"
        ), {"id": turn_id, "now": now}).scalar_one()
        db.execute(text(
            "UPDATE agent_turn_dispatch_outbox SET state = 'resolved_cancelled', "
            "resolved_at = :now, resolution = 'cancelled' "
            "WHERE turn_id = :id AND state IN ('pending', 'delivered', 'delivered_unclaimed')"
        ), {"id": turn_id, "now": now})
        if turn["session_id"]:
            db.execute(text(
                "UPDATE agent_sessions SET active_turn_id = NULL, updated_at = :now "
                "WHERE id = :sid AND active_turn_id = :tid"
            ), {"sid": turn["session_id"], "tid": turn_id, "now": now})
        db.commit()
        return {"turn_id": turn_id, "status": "cancelled", "dispatch_generation": new_generation}
    if turn["status"] in ("running", "awaiting_approval", "awaiting_clarification", "reconciling"):
        result = db.execute(text(
            "UPDATE agent_turns SET status = 'cancelling', cancel_requested_at = :now, "
            "updated_at = :now WHERE id = :id AND status = :old"
        ), {"id": turn_id, "old": turn["status"], "now": now})
        if result.rowcount != 1:
            db.rollback()
            raise DispatchError("TURN_CANCEL_RACE")
        db.commit()
        return {"turn_id": turn_id, "status": "cancelling", "signal": "cancel"}
    if turn["status"] == "cancelling":
        db.commit()
        return {"turn_id": turn_id, "status": "cancelling", "signal": "cancel"}
    db.rollback()
    raise DispatchError("TURN_TERMINAL")
