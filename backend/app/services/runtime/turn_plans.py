"""Governed-action proposals created FROM an Agent turn's own persisted
evidence (Task 3, business-journey acceptance).

`create_governed_plan_from_turn` is the service behind the Runtime UI
endpoint `POST /api/v2/runtime/action-plans/from-turn`: it reads a
succeeded turn's persisted tool evidence (`AgentToolExecution`) and
immutable final response (the `agent_messages` row `AgentTurn.
response_message_id` points at), validates the caller-supplied target and
payload digest, and persists exactly one new, independent
`GovernedTurnPlan` row — never a `RuntimePlan` (which needs a materialized
`SemanticSnapshot` + catalog `Action` a plain conversational turn never
produces) and never an `AgentApproval` (which is wired into that SAME
turn's own dispatch/resume state machine). It makes no model call.

`decide_governed_plan` is the only way a `GovernedTurnPlan` ever leaves
`pending`: "approved" performs one real, self-contained mutation of the
target `EntityInstance.row_data` (so `target_before_hash != target_after_hash`
is a genuine, observable fact, not a fabricated one) and records a receipt;
"rejected" mutates nothing. A plan whose `expiry` has passed can never be
decided (`GOVERNED_PLAN_EXPIRED`) — the "expired" branch reaches that state
by `create_governed_plan_from_turn` pinning its `expiry` to the moment of
creation, not by a background sweep.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.runtime_execution import GovernedTurnPlan
from app.services.governance_audit import canonical_event, event_hash, partition_key_for

GOVERNED_PLAN_TTL_SECONDS = 900
BRANCHES = ("approved", "rejected", "expired")


class TurnPlanError(Exception):
    """Rejected governed-turn-plan operation. `reason_code` mirrors
    `RuntimeAccessError`/`ExecutionError`/`BindingError`/`SandboxError` —
    stable, safe to surface to a caller."""

    def __init__(self, reason_code: str, message: str | None = None):
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {message}" if message else reason_code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(text_value: str) -> str:
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(_canonical_json(value))


def _decode_row_data(value: Any) -> dict:
    """`EntityInstance.row_data` is a JSON column; a raw `text()` query (used
    throughout this module, matching the rest of `app.services.runtime`)
    round-trips it as a Python dict on PostgreSQL but as a raw JSON string on
    the SQLite unit harness — decode it uniformly so a hash/mutation never
    operates on the wrong Python type."""
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value) if value else {}
    return dict(value or {})


@dataclass(frozen=True)
class GovernedActionPlan:
    """The wire/return shape of `create_governed_plan_from_turn`."""

    action_plan_id: str
    plan_hash: str
    approval_id: str
    branch: str
    target_fixture_id: str
    status: str
    correlation_id: str


@dataclass(frozen=True)
class GovernedActionPlanView:
    """Read projection of one persisted `GovernedTurnPlan`, with "expired"
    computed at read time rather than stored (see module docstring)."""

    action_plan_id: str
    plan_hash: str
    approval_id: str
    turn_id: str
    branch: str
    target_fixture_id: str
    status: str
    execution_class: str
    target_before_hash: str
    target_after_hash: str
    must_write: bool
    receipt_id: str | None
    audit_event_id: str | None
    correlation_id: str


def _correlation(operation: str, plan_id: str) -> str:
    return f"governed-turn-plan:{operation}:{plan_id}"


def _load_turn(db: Session, *, turn_id: str, actor_user_id: str) -> Mapping[str, Any]:
    row = db.execute(text(
        "SELECT t.id, t.status, t.response_message_id, t.session_id, "
        "s.agent_id, s.owner_user_id, m.content AS response_content "
        "FROM agent_turns t "
        "JOIN agent_sessions s ON s.id = t.session_id "
        "LEFT JOIN agent_messages m ON m.id = t.response_message_id "
        "WHERE t.id = :id"
    ), {"id": turn_id}).mappings().one_or_none()
    if row is None or row["owner_user_id"] != actor_user_id:
        # Existence-hiding, mirroring every other Runtime/turn read in this
        # codebase: a turn owned by a different user is indistinguishable
        # from one that does not exist.
        raise TurnPlanError("TURN_NOT_FOUND")
    if row["status"] != "succeeded" or not row["response_message_id"]:
        raise TurnPlanError("FINAL_RESPONSE_MISSING", f"turn status={row['status']!r}")
    if not (row["response_content"] or "").strip():
        raise TurnPlanError("FINAL_RESPONSE_MISSING", "empty response content")
    return row


def _load_tool_evidence(db: Session, *, turn_id: str) -> Mapping[str, Any]:
    row = db.execute(text(
        "SELECT id, status, descriptor, parameters_hash, result_hash "
        "FROM agent_tool_executions WHERE turn_id = :turn ORDER BY created_at ASC LIMIT 1"
    ), {"turn": turn_id}).mappings().one_or_none()
    if row is None:
        raise TurnPlanError("TOOL_EVIDENCE_MISSING", "turn has no persisted tool execution")
    return row


def _bound_ontology_ids(db: Session, *, agent_id: str) -> tuple[str, ...]:
    rows = db.execute(text(
        "SELECT b.ontology_id FROM agent_ontology_bindings b "
        "JOIN agents a ON a.active_version_id = b.agent_version_id "
        "WHERE a.id = :agent"
    ), {"agent": agent_id}).mappings().all()
    return tuple(r["ontology_id"] for r in rows)


def _manifest_entity_ids(db: Session, *, ontology_id: str) -> frozenset[str]:
    row = db.execute(text(
        "SELECT r.manifest_projection FROM ontology_projects p "
        "JOIN ontology_releases r ON r.id = p.latest_published_release_id "
        "WHERE p.id = :o"
    ), {"o": ontology_id}).mappings().one_or_none()
    if row is None:
        return frozenset()
    projection = row["manifest_projection"]
    if isinstance(projection, (str, bytes, bytearray)):
        try:
            projection = json.loads(projection)
        except (TypeError, ValueError):
            return frozenset()
    if not isinstance(projection, dict):
        return frozenset()
    entities = projection.get("entities") or []
    return frozenset(e["id"] for e in entities if isinstance(e, dict) and "id" in e)


def _resolve_disposable_target(db: Session, *, agent_id: str, target_fixture_id: str) -> Mapping[str, Any]:
    """A target is "the disposable fixture" only if it is a LIVE
    `EntityInstance` under an ontology this turn's Agent is actually bound
    to, whose entity type the bound ontology's own published release
    manifest declares. Anything else — a bare guess, a foreign ontology's
    row, a production identifier — is "outside the disposable fixture"."""
    ontology_ids = _bound_ontology_ids(db, agent_id=agent_id)
    if not ontology_ids:
        raise TurnPlanError("TARGET_OUTSIDE_DISPOSABLE_FIXTURE", "agent has no bound ontology")
    row = db.execute(text(
        "SELECT id, ontology_id, entity_id, row_data FROM entity_instances "
        "WHERE id = :id AND deleted_at IS NULL"
    ), {"id": target_fixture_id}).mappings().one_or_none()
    if row is None or row["ontology_id"] not in ontology_ids:
        raise TurnPlanError("TARGET_OUTSIDE_DISPOSABLE_FIXTURE", "no live instance under a bound ontology")
    manifest_entity_ids = _manifest_entity_ids(db, ontology_id=row["ontology_id"])
    if row["entity_id"] not in manifest_entity_ids:
        raise TurnPlanError("TARGET_OUTSIDE_DISPOSABLE_FIXTURE", "entity type not in published manifest")
    return {**row, "row_data": _decode_row_data(row["row_data"])}


def _expected_payload_digest(
    *, turn_id: str, tool_execution_id: str, tool_result_hash: str | None,
    response_content: str, branch: str, target_fixture_id: str,
) -> str:
    return _sha256_json({
        "turn_id": turn_id,
        "tool_execution_id": tool_execution_id,
        "tool_result_hash": tool_result_hash,
        "final_response_hash": _sha256_text(response_content),
        "branch": branch,
        "target_fixture_id": target_fixture_id,
    })


def create_governed_plan_from_turn(
    db: Session,
    *,
    turn_id: str,
    branch: str,
    target_fixture_id: str,
    idempotency_key: str,
    payload_digest: str,
    actor_user_id: str,
) -> GovernedActionPlan:
    if branch not in BRANCHES:
        raise TurnPlanError("BRANCH_INVALID", f"{branch!r}")
    if not idempotency_key:
        raise TurnPlanError("IDEMPOTENCY_KEY_REQUIRED")

    existing = db.execute(text(
        "SELECT id FROM governed_turn_plans WHERE idempotency_key = :key"
    ), {"key": idempotency_key}).mappings().one_or_none()
    if existing is not None:
        raise TurnPlanError("IDEMPOTENCY_KEY_REUSED", f"{idempotency_key!r}")

    turn = _load_turn(db, turn_id=turn_id, actor_user_id=actor_user_id)
    tool_execution = _load_tool_evidence(db, turn_id=turn_id)
    target = _resolve_disposable_target(db, agent_id=turn["agent_id"], target_fixture_id=target_fixture_id)

    expected_digest = _expected_payload_digest(
        turn_id=turn_id, tool_execution_id=tool_execution["id"],
        tool_result_hash=tool_execution["result_hash"], response_content=turn["response_content"],
        branch=branch, target_fixture_id=target_fixture_id,
    )
    if payload_digest != expected_digest:
        raise TurnPlanError("PAYLOAD_DIGEST_MISMATCH")

    now = _now()
    plan_id = _new_id()
    before_hash = _sha256_json(target["row_data"])
    plan_hash = _sha256_json({
        "plan_id": plan_id,
        "turn_id": turn_id,
        "tool_execution_id": tool_execution["id"],
        "branch": branch,
        "target_fixture_id": target_fixture_id,
        "idempotency_key": idempotency_key,
        "payload_digest": payload_digest,
    })
    correlation_id = _correlation("create", plan_id)
    # The "expired" branch is pinned already-expired at creation time (see
    # module docstring) rather than waiting out a real TTL or requiring a
    # background sweep; every other branch gets the normal window.
    expiry = now if branch == "expired" else now + timedelta(seconds=GOVERNED_PLAN_TTL_SECONDS)

    row = GovernedTurnPlan(
        id=plan_id,
        turn_id=turn_id,
        tool_execution_id=tool_execution["id"],
        branch=branch,
        target_fixture_id=target_fixture_id,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        plan_hash=plan_hash,
        status="pending",
        target_before_hash=before_hash,
        target_after_hash=before_hash,
        expiry=expiry,
        correlation_id=correlation_id,
        created_at=now,
    )
    db.add(row)
    db.commit()

    return GovernedActionPlan(
        action_plan_id=plan_id, plan_hash=plan_hash, approval_id=plan_id,
        branch=branch, target_fixture_id=target_fixture_id, status="pending",
        correlation_id=correlation_id,
    )


def _append_governance_audit_event(
    db: Session, *, security_domain_id: str, operation: str, decision: str,
    correlation_id: str, actor_user_id: str, lineage: Mapping[str, Any],
) -> str:
    """A minimal, cross-dialect append to the SAME `governance_audit_logs`
    chain `app.services.governance_audit.append_audit` writes — reusing its
    pure canonicalization/hashing helpers, but reading the chain head WITHOUT
    `FOR UPDATE` (Postgres/MySQL-only syntax the SQLite unit harness cannot
    run at all). This governed-turn-plan decision path is a low-frequency,
    one-at-a-time human action, not the high-concurrency writer path
    `append_audit` itself guards — so the missing row lock is an accepted,
    documented gap here, not a silent regression of that other module."""
    now = datetime.now(timezone.utc)
    partition_key = partition_key_for(security_domain_id, now)
    db.execute(text(
        "INSERT INTO governance_audit_chain_heads (partition_key, security_domain_id, next_sequence, last_hash) "
        "VALUES (:partition, :domain, 1, NULL) "
        "ON CONFLICT (partition_key) DO NOTHING"
    ), {"partition": partition_key, "domain": security_domain_id})
    head = db.execute(text(
        "SELECT next_sequence, last_hash FROM governance_audit_chain_heads WHERE partition_key = :partition"
    ), {"partition": partition_key}).mappings().one()
    sequence = head["next_sequence"]
    previous_hash = bytes(head["last_hash"]) if head["last_hash"] is not None else None
    event = {
        "security_domain_id": security_domain_id, "partition_key": partition_key, "sequence": sequence,
        "actor_user_id": actor_user_id, "operation": operation, "decision": decision,
        "policy_ids": {}, "correlation_id": correlation_id, "input_hash": None, "output_hash": None,
        "lineage": dict(lineage), "outcome": "SUCCEEDED", "previous_hash": previous_hash.hex() if previous_hash else None,
        "agent_id": None, "agent_version_id": None, "release_id": None, "model_version_id": None,
        "connection_version_id": None, "retention_class": "standard",
        "occurred_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    digest = event_hash(event)
    row_id = _new_id()
    db.execute(text(
        "INSERT INTO governance_audit_logs "
        "(id, security_domain_id, partition_key, sequence, actor_user_id, operation, decision, "
        "policy_ids, correlation_id, input_hash, output_hash, lineage, outcome, previous_hash, "
        "event_hash, retention_class, occurred_at) "
        "VALUES (:id, :domain, :partition, :sequence, :actor, :operation, :decision, "
        ":policy_ids, :correlation_id, :input_hash, :output_hash, :lineage, :outcome, :previous_hash, "
        ":event_hash, :retention_class, :occurred_at)"
    ), {
        "id": row_id, "domain": security_domain_id, "partition": partition_key, "sequence": sequence,
        "actor": actor_user_id, "operation": operation, "decision": decision,
        "policy_ids": json.dumps({}), "correlation_id": correlation_id, "input_hash": None, "output_hash": None,
        "lineage": json.dumps(dict(lineage), ensure_ascii=False), "outcome": "SUCCEEDED",
        "previous_hash": previous_hash, "event_hash": digest, "retention_class": "standard", "occurred_at": now,
    })
    db.execute(text(
        "UPDATE governance_audit_chain_heads SET next_sequence = :next, last_hash = :hash "
        "WHERE partition_key = :partition"
    ), {"next": sequence + 1, "hash": digest, "partition": partition_key})
    return row_id


def _plan_status(row: GovernedTurnPlan, *, now: datetime) -> str:
    if row.status != "pending":
        return row.status
    if now >= _as_aware_utc(row.expiry):
        return "expired"
    return "pending"


def decide_governed_plan(
    db: Session,
    *,
    plan_id: str,
    presented_plan_hash: str,
    decision: str,
    actor_user_id: str,
    security_domain_id: str,
) -> GovernedActionPlanView:
    """Transition a `pending` plan to `approved`/`rejected`. Only "approved"
    performs a real mutation of the target's `row_data`; the plan and its
    `EntityInstance` row are updated in the SAME transaction, so a reader can
    never observe a plan marked approved whose target has not actually
    changed."""
    if decision not in ("approved", "rejected"):
        raise TurnPlanError("DECISION_INVALID", f"{decision!r}")

    row = db.query(GovernedTurnPlan).filter(GovernedTurnPlan.id == plan_id).one_or_none()
    if row is None:
        raise TurnPlanError("PLAN_NOT_FOUND")
    turn_owner = db.execute(text(
        "SELECT s.owner_user_id FROM agent_turns t JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE t.id = :turn"
    ), {"turn": row.turn_id}).mappings().one_or_none()
    if turn_owner is None or turn_owner["owner_user_id"] != actor_user_id:
        raise TurnPlanError("PLAN_NOT_FOUND")
    if presented_plan_hash != row.plan_hash:
        raise TurnPlanError("PLAN_HASH_MISMATCH")

    now = _now()
    current_status = _plan_status(row, now=now)
    if current_status == "expired":
        raise TurnPlanError("GOVERNED_PLAN_EXPIRED")
    if current_status != "pending":
        raise TurnPlanError("GOVERNED_PLAN_ALREADY_DECIDED", f"current status={current_status}")

    receipt_id = _new_id()
    if decision == "approved":
        instance = db.execute(text(
            "SELECT row_data FROM entity_instances WHERE id = :id"
        ), {"id": row.target_fixture_id}).mappings().one()
        mutated = _decode_row_data(instance["row_data"])
        mutated["_governed_turn_plan_applied"] = plan_id
        # No `CAST(... AS json)` here: SQLite's generic-type CAST rules give
        # an unrecognized type name like "json" NUMERIC affinity, which
        # silently corrupts a JSON string parameter to the integer 0 (verified
        # directly against this checkout's Python/SQLite) — a real, sharp
        # footgun `EntityInstance.row_data`'s own JSON-typed column already
        # avoids elsewhere by never using this cast. The raw JSON string is
        # written as-is; `_decode_row_data` already normalizes the
        # string-vs-dict duality on every read.
        db.execute(text(
            "UPDATE entity_instances SET row_data = :data, revision = revision + 1, "
            "updated_at = :now WHERE id = :id"
        ), {"data": json.dumps(mutated, ensure_ascii=False), "now": now, "id": row.target_fixture_id})
        row.target_after_hash = _sha256_json(mutated)
    audit_event_id = _append_governance_audit_event(
        db,
        security_domain_id=security_domain_id,
        operation="runtime.governed_turn_plan.decide",
        decision=decision,
        correlation_id=_correlation(decision, plan_id),
        actor_user_id=actor_user_id,
        lineage={"plan_id": plan_id, "turn_id": row.turn_id, "branch": row.branch, "decision": decision},
    )

    row.status = decision
    row.receipt_id = receipt_id
    row.audit_event_id = audit_event_id
    row.decided_by_user_id = actor_user_id
    row.decided_at = now
    db.commit()
    db.refresh(row)

    return _to_view(row, now=now)


def get_governed_plan(db: Session, *, plan_id: str, actor_user_id: str) -> GovernedActionPlanView:
    """Read-only projection. Never mutates a row, including an
    already-lapsed "expired" one — its terminal status is computed from
    `expiry`, not written back here."""
    row = db.query(GovernedTurnPlan).filter(GovernedTurnPlan.id == plan_id).one_or_none()
    if row is None:
        raise TurnPlanError("PLAN_NOT_FOUND")
    turn_owner = db.execute(text(
        "SELECT s.owner_user_id FROM agent_turns t JOIN agent_sessions s ON s.id = t.session_id "
        "WHERE t.id = :turn"
    ), {"turn": row.turn_id}).mappings().one_or_none()
    if turn_owner is None or turn_owner["owner_user_id"] != actor_user_id:
        raise TurnPlanError("PLAN_NOT_FOUND")
    return _to_view(row, now=_now())


def _to_view(row: GovernedTurnPlan, *, now: datetime) -> GovernedActionPlanView:
    status = _plan_status(row, now=now)
    return GovernedActionPlanView(
        action_plan_id=row.id,
        plan_hash=row.plan_hash,
        approval_id=row.id,
        turn_id=row.turn_id,
        branch=row.branch,
        target_fixture_id=row.target_fixture_id,
        status=status,
        execution_class="HUMAN_APPROVED",
        target_before_hash=row.target_before_hash,
        target_after_hash=row.target_after_hash,
        must_write=row.branch == "approved",
        receipt_id=row.receipt_id,
        audit_event_id=row.audit_event_id,
        correlation_id=row.correlation_id or "",
    )


__all__ = [
    "GOVERNED_PLAN_TTL_SECONDS",
    "GovernedActionPlan",
    "GovernedActionPlanView",
    "TurnPlanError",
    "create_governed_plan_from_turn",
    "decide_governed_plan",
    "get_governed_plan",
]
