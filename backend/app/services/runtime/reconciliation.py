"""Agent reconciliation (P5C-EXECUTE) and Runtime execution reconciliation
(Task 26).

Operator resolution of unknown outcomes: `confirm_not_accepted_and_retry`
resumes the Turn with a new dispatch generation; `confirm_succeeded` /
`confirm_failed` terminalize.  Unresolved uncertainty cannot select replay;
a stale base revision conflicts.

The functions above this line (`list_cases`/`get_case`/`resolve_case`) are
the original P5C authority over `agent_reconciliation_cases`/`agent_turns` —
a tool-call-execution domain, untouched by Task 26.

`create_reconciliation_case`/`get_reconciliation_case` below are a distinct,
additive Task 26 authority over the new `runtime_reconciliation_cases` table
(`app.models.runtime_execution.RuntimeReconciliationCase`): a case opened by
`app.services.runtime.execution.execute_plan` whenever a write's outcome is
genuinely `UNKNOWN` (an ambiguous timeout/connection failure — never a
definite failure, which gets its own status directly on the `RuntimeExecution`
row instead). This mirrors the precedent already established for Task 23's
`approve_exact_plan` living alongside the P5B `agent_approvals` functions in
`app.services.actions.approval` (see that file's "Runtime exact-plan
approval" section): a different subject (an immutable, snapshot-pinned
`RuntimePlan`, not a proposed tool-call execution) gets its own additive
authority in the same module, reusing only what genuinely generalizes
(naming/`_now`/`_new_id` conventions) — never the P5C table or its CAS/resume
machinery, which has no meaning for a `RuntimePlan` execution.

`UNKNOWN` never transitions to a replay of the SAME execution: nothing in
this module (or `execution.py`) ever re-invokes a writer for a case recorded
here. `retry_unknown` (`app.services.runtime.execution`) always returns
`False` — a human must resolve the case (see `resolve_reconciliation_case`
below for the one and only closing action this module offers), and if a
retry is genuinely warranted, submit an entirely new governed plan through
`RuntimeService.create_action_plan` and execute *that* plan — never resume
this one.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.runtime_execution import RuntimeReconciliationCase


class ReconciliationError(Exception):
    """Rejected reconciliation operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def list_cases(db: Session, *, status: str | None = None, limit: int = 50) -> dict:
    params: dict = {"limit": limit}
    where = ""
    if status:
        where = "WHERE c.state = :status"
        params["status"] = status
    rows = db.execute(text(
        "SELECT c.id, c.turn_id, c.execution_kind, c.execution_id, c.revision, c.state, "
        "c.request_hash, c.created_at, c.updated_at "
        "FROM agent_reconciliation_cases c " + where + " ORDER BY c.created_at LIMIT :limit"
    ), params).mappings().all()
    return {"items": [dict(r) for r in rows], "next_cursor": None, "has_more": False}


def get_case(db: Session, *, case_id: str) -> dict:
    """Section 12 reconciliation detail: the redacted persisted case row."""
    row = db.execute(text(
        "SELECT id, turn_id, execution_kind, execution_id, revision, state, "
        "request_hash, created_at, updated_at "
        "FROM agent_reconciliation_cases WHERE id = :id"
    ), {"id": case_id}).mappings().one_or_none()
    if row is None:
        raise ReconciliationError("CASE_NOT_FOUND")
    return dict(row)


def resolve_case(db: Session, *, case_id: str, base_revision: int, resolution: str,
                 evidence: str | None = None, actor_id: str | None = None) -> dict:
    """CAS the open case to resolved_succeeded/resolved_failed (terminal) or
    resolved_retry (resume with a new dispatch generation)."""
    row = db.execute(text(
        "SELECT c.id, c.turn_id, c.revision, c.state FROM agent_reconciliation_cases c "
        "WHERE c.id = :id FOR UPDATE"
    ), {"id": case_id}).mappings().one_or_none()
    if row is None:
        db.rollback()
        raise ReconciliationError("CASE_NOT_FOUND")
    if row["state"] != "open":
        db.rollback()
        raise ReconciliationError("CASE_ALREADY_RESOLVED")
    if row["revision"] != base_revision:
        db.rollback()
        raise ReconciliationError("RECONCILIATION_CONFLICT")
    new_state = {"succeeded": "resolved_succeeded", "failed": "resolved_failed",
                 "retry": "resolved_retry"}.get(resolution)
    if new_state is None:
        db.rollback()
        raise ReconciliationError("RESOLUTION_INVALID")
    db.execute(text(
        "UPDATE agent_reconciliation_cases SET state = :state, revision = revision + 1, "
        "evidence_hash = :eh, resolver_id = :actor, updated_at = now() "
        "WHERE id = :id AND state = 'open' AND revision = :rev"
    ), {"state": new_state, "id": case_id, "rev": base_revision,
        "eh": _new_id(), "actor": actor_id})
    if resolution == "retry":
        # operator-confirmed not-run: resume the Turn with a fresh generation
        new_generation = db.execute(text(
            "UPDATE agent_turns SET status = 'queued', dispatch_generation = dispatch_generation + 1, "
            "updated_at = now() WHERE id = :id RETURNING dispatch_generation"
        ), {"id": row["turn_id"]}).scalar_one_or_none()
        if new_generation is not None:
            db.execute(text(
                "INSERT INTO agent_turn_dispatch_outbox (id, turn_id, dispatch_generation, "
                "operation, state, created_at) "
                "VALUES (:id, :turn, :gen, 'delivery_recovery', 'pending', now())"
            ), {"id": _new_id(), "turn": row["turn_id"], "gen": new_generation})
    else:
        db.execute(text(
            "UPDATE agent_turns SET status = 'failed', error_code = 'RECONCILIATION_' || :res, "
            "updated_at = now() WHERE id = :id"
        ), {"id": row["turn_id"], "res": resolution.upper()})
    db.commit()
    return {"case_id": case_id, "state": new_state, "resolution": resolution,
            "resumed": resolution == "retry"}


# --- Runtime execution reconciliation (Task 26) ----------------------------


@dataclass(frozen=True)
class ReconciliationCase:
    """Immutable read projection of one persisted `RuntimeReconciliationCase`
    row (`app.models.runtime_execution`)."""

    id: str
    execution_id: str
    plan_id: str
    status: str
    unknown_reason: str
    observed_effect: Mapping[str, Any]
    next_action: str
    created_at: datetime


def _to_reconciliation_case(row: RuntimeReconciliationCase) -> ReconciliationCase:
    return ReconciliationCase(
        id=row.id, execution_id=row.execution_id, plan_id=row.plan_id, status=row.status,
        unknown_reason=row.unknown_reason, observed_effect=dict(row.observed_effect),
        next_action=row.next_action, created_at=row.created_at,
    )


def create_reconciliation_case(
    db: Session, *, execution_id: str, plan_id: str, unknown_reason: str,
    observed_effect: Mapping[str, Any] | None = None, next_action: str = "human_review",
) -> ReconciliationCase:
    """Open a new `runtime_reconciliation_cases` row for an `UNKNOWN`
    execution outcome. Never called for a definite (`SUCCEEDED`/`FAILED`)
    outcome — see `app.services.runtime.execution.execute_plan`."""
    row = RuntimeReconciliationCase(
        id=str(uuid.uuid4()), execution_id=execution_id, plan_id=plan_id, status="open",
        unknown_reason=unknown_reason, observed_effect=dict(observed_effect or {}), next_action=next_action,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_reconciliation_case(row)


def get_reconciliation_case(db: Session, *, case_id: str) -> ReconciliationCase | None:
    row = db.get(RuntimeReconciliationCase, case_id)
    return _to_reconciliation_case(row) if row is not None else None


def resolve_reconciliation_case(
    db: Session, *, case_id: str, resolution: str,
) -> ReconciliationCase:
    """Terminalize an open case as `resolved_succeeded` or `resolved_failed`
    — the operator's own confirmation of what actually happened, based on
    evidence gathered outside this module (e.g. inspecting the target row
    directly). There is no `resolved_retry`/replay resolution: closing a
    case here never causes anything to re-execute. A genuinely warranted
    retry is an entirely new governed plan through
    `RuntimeService.create_action_plan`, executed via a fresh call to
    `execute_plan` — never a resumption of the execution this case
    describes."""
    row = db.get(RuntimeReconciliationCase, case_id)
    if row is None:
        raise ReconciliationError("CASE_NOT_FOUND")
    if row.status != "open":
        raise ReconciliationError("CASE_ALREADY_RESOLVED")
    new_status = {"succeeded": "resolved_succeeded", "failed": "resolved_failed"}.get(resolution)
    if new_status is None:
        raise ReconciliationError("RESOLUTION_INVALID")
    row.status = new_status
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_reconciliation_case(row)
