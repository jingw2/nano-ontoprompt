"""Durable managed webhook/outbox ingestion and event refresh execution."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v2.connection import Connection
from app.models.v2.refresh import RefreshDeadLetter, RefreshInboxEvent, RefreshRun, RefreshSourceState
from app.schemas.refresh import (
    ChangeEnvelope,
    ConfigurationDriftError,
    RefreshCancellationRequested,
    RefreshError,
    RefreshFencingError,
    RefreshLeaseError,
    RefreshPolicy,
    SourceCursor,
    dedupe_key,
)
from app.services.v2.incremental import polling
from app.services.v2.incremental.contract import (
    _cursor_to_json,
    _lock_or_create_source_state,
    assert_refresh_not_cancelled,
    claim_refresh_run,
    finalize_refresh_cancellation,
    mark_refresh_retryable,
    record_refresh_outcome,
    request_refresh_cancellation,
)
from app.tasks.topology import QUEUE_REFRESH_EVENT

from .event_adapters import EventIngressError


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _event_contract(envelope: ChangeEnvelope) -> str:
    if envelope.source_cursor is not None:
        return "opaque_source_cursor"
    return "watermark_primary_key"


def _event_idempotency_key(*, source_id: str, resource: str, event_id: str) -> str:
    return f"event:{source_id}:{resource}:{event_id}"


def _envelope_to_json(envelope: ChangeEnvelope) -> dict[str, object]:
    return {
        "event_id": envelope.event_id,
        "source_id": envelope.source_id,
        "resource": envelope.resource,
        "operation": envelope.operation,
        "primary_key": envelope.primary_key,
        "payload": dict(envelope.payload),
        "watermark": envelope.watermark,
        "source_cursor": envelope.source_cursor,
        "schema_hash": envelope.schema_hash,
        "occurred_at": envelope.occurred_at.isoformat() if envelope.occurred_at else None,
        "received_at": _as_utc(envelope.received_at).isoformat(),
        "contract": _event_contract(envelope),
        "version": 1,
    }


def _envelope_from_json(value: Mapping[str, object], *, received_at: datetime) -> ChangeEnvelope:
    from app.services.v2.incremental.event_adapters import _normalize_record

    return _normalize_record(value, source_id=str(value.get("source_id") or ""), received_at=_as_utc(received_at))


@dataclass(frozen=True)
class IngestReceipt:
    """Non-secret result of durable inbox acceptance."""

    status: str
    inbox_id: str
    run_id: str | None
    source_id: str
    resource: str
    event_id: str
    fencing_token: int | None = None
    cursor: SourceCursor | None = None
    dead_letter_id: str | None = None
    reason_code: str = "ACCEPTED"

    @property
    def source_cursor(self) -> SourceCursor | None:
        return self.cursor

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "inbox_id": self.inbox_id,
            "run_id": self.run_id,
            "source_id": self.source_id,
            "resource": self.resource,
            "event_id": self.event_id,
            "fencing_token": self.fencing_token,
            "cursor": _cursor_to_json(self.cursor),
            "dead_letter_id": self.dead_letter_id,
            "reason_code": self.reason_code,
        }


def _dead_letter_for_run(db: Session, run_id: str) -> RefreshDeadLetter | None:
    return db.execute(
        select(RefreshDeadLetter).where(RefreshDeadLetter.run_id == run_id).order_by(RefreshDeadLetter.created_at.desc())
    ).scalars().first()


def _dead_letter_for_inbox(db: Session, row: RefreshInboxEvent) -> RefreshDeadLetter | None:
    return db.execute(
        select(RefreshDeadLetter).where(
            RefreshDeadLetter.source_id == row.source_id,
            RefreshDeadLetter.resource == row.resource,
            RefreshDeadLetter.event_id == row.event_id,
            RefreshDeadLetter.run_id.is_(None),
        ).order_by(RefreshDeadLetter.created_at.desc())
    ).scalars().first()


def _receipt(db: Session, row: RefreshInboxEvent, *, status: str, reason_code: str = "ACCEPTED") -> IngestReceipt:
    run = db.get(RefreshRun, row.run_id) if row.run_id else None
    letter = _dead_letter_for_run(db, run.id) if run is not None else _dead_letter_for_inbox(db, row)
    state = db.execute(
        select(RefreshSourceState).where(
            RefreshSourceState.source_id == row.source_id,
            RefreshSourceState.resource == row.resource,
        )
    ).scalar_one_or_none()
    return IngestReceipt(
        status=status,
        inbox_id=row.id,
        run_id=row.run_id,
        source_id=row.source_id,
        resource=row.resource,
        event_id=row.event_id,
        fencing_token=run.fencing_token if run is not None else (state.fencing_token if state is not None else None),
        cursor=(run.cursor_after or run.cursor_before) if run is not None else (state.cursor if state is not None else None),
        dead_letter_id=letter.id if letter is not None else None,
        reason_code=reason_code,
    )


class EventIngestService:
    """Persist a managed event before dispatch and execute it under the
    existing source lease/fencing/cancellation contract."""

    dead_letter_model = RefreshDeadLetter

    def __init__(self, *, max_attempts: int = 3):
        # The durable inbox delivery counter is bounded by the same explicit
        # retry budget used by refresh execution.  This prevents a broker
        # outage from keeping a row retryable forever.
        self.max_attempts = max(1, min(20, int(max_attempts)))

    @staticmethod
    def _publish_retry_delay(attempt: int) -> int:
        """Return a bounded exponential delay for broker redelivery."""
        return min(300, 15 * (2 ** max(0, attempt - 1)))

    def _validate_source_contract(
        self, db: Session, *, state: RefreshSourceState, envelope: ChangeEnvelope,
    ) -> None:
        event_contract = _event_contract(envelope)
        if state.cursor_contract != event_contract:
            db.rollback()
            raise EventIngressError("SCHEMA_DRIFT", "event cursor contract does not match source configuration")
        configured = state.configuration or {}
        expected_schema = configured.get("schema_hash")
        if expected_schema and envelope.schema_hash and expected_schema != envelope.schema_hash:
            db.rollback()
            raise EventIngressError("SCHEMA_DRIFT", "event schema hash does not match source configuration")
        if not expected_schema and envelope.schema_hash:
            state.configuration = {**configured, "schema_hash": envelope.schema_hash}

    def accept(
        self,
        db: Session,
        envelope: ChangeEnvelope,
        *,
        lease_owner: str,
        now: datetime,
    ) -> IngestReceipt:
        """Durably record an event and claim its run before any dispatch."""
        now = _as_utc(now)
        if envelope.source_id == "" or envelope.resource == "" or envelope.event_id == "":
            raise EventIngressError("INVALID_CHANGE_ENVELOPE", "source, resource, and event identity are required")
        event_hash = dedupe_key(envelope)

        # Identity and immutable hash are the first lookup.  A retry of an
        # already accepted event must remain an acknowledgement even when the
        # mutable source/schema revision has since changed.
        row = db.execute(
            select(RefreshInboxEvent).where(
                RefreshInboxEvent.source_id == envelope.source_id,
                RefreshInboxEvent.resource == envelope.resource,
                RefreshInboxEvent.event_id == envelope.event_id,
            ).with_for_update()
        ).scalar_one_or_none()
        if row is not None:
            if row.event_hash and row.event_hash != event_hash:
                db.rollback()
                raise EventIngressError("REPLAY_DETECTED", "event identity was reused with a different payload")
            if row.event_hash is None:
                row.event_hash = event_hash
            if row.state == "dead_lettered":
                status = "dead_lettered"
                reason_code = "DEAD_LETTERED"
            elif row.state == "processed":
                status = "processed"
                reason_code = "DUPLICATE_EVENT"
            else:
                row.state = "duplicate"
                status = "duplicate"
                reason_code = "DUPLICATE_EVENT"
            db.commit()
            return _receipt(db, row, status=status, reason_code=reason_code)

        connection = db.get(Connection, envelope.source_id)
        connection_config = dict(connection.config or {}) if connection is not None else {}
        configured_contract = (
            getattr(connection, "cursor_contract", None)
            or connection_config.get("cursor_contract")
            or _event_contract(envelope)
        )
        if configured_contract not in {"watermark_primary_key", "opaque_source_cursor"}:
            raise EventIngressError("SCHEMA_DRIFT", "source cursor contract is not supported")
        if configured_contract != _event_contract(envelope):
            # Reject a first event against the durable connection contract
            # before provisioning a source-state row.  This keeps a malformed
            # first delivery from creating an orphaned source configuration.
            raise EventIngressError("SCHEMA_DRIFT", "event cursor contract does not match source configuration")
        state = db.execute(
            select(RefreshSourceState).where(
                RefreshSourceState.source_id == envelope.source_id,
                RefreshSourceState.resource == envelope.resource,
            ).with_for_update()
        ).scalar_one_or_none()
        if state is None:
            state = _lock_or_create_source_state(
                db, source_id=envelope.source_id, resource=envelope.resource,
                now=now, default_cursor_contract=configured_contract,
            )
        if not state.configuration and connection_config.get("schema_hash"):
            state.configuration = {"schema_hash": connection_config["schema_hash"]}
        self._validate_source_contract(db, state=state, envelope=envelope)

        row = RefreshInboxEvent(
            id=str(uuid.uuid4()), source_id=envelope.source_id, resource=envelope.resource,
            event_id=envelope.event_id, event_hash=event_hash, state="received",
            received_at=_as_utc(envelope.received_at), envelope_json=_envelope_to_json(envelope),
        )
        db.add(row)
        # This commit is the durable inbox-before-dispatch boundary.  A
        # broker outage or source lease conflict leaves the event replayable.
        db.commit()
        db.refresh(row)

        try:
            run = claim_refresh_run(
                db, source_id=envelope.source_id, resource=envelope.resource,
                policy=RefreshPolicy.EVENT_DRIVEN,
                idempotency_key=_event_idempotency_key(
                    source_id=envelope.source_id, resource=envelope.resource, event_id=envelope.event_id,
                ),
                lease_owner=lease_owner, now=now, default_cursor_contract=state.cursor_contract,
            )
        except RefreshLeaseError:
            # The inbox commit is durable even when another event currently
            # owns this source lease.  A later drain will claim this row after
            # the owner finishes or its lease expires.
            db.rollback()
            row = db.get(RefreshInboxEvent, row.id)
            return _receipt(db, row, status="received", reason_code="PENDING_LEASE")
        run.trigger = "event"
        run.dispatch_queue = QUEUE_REFRESH_EVENT
        db.commit()

        row = db.get(RefreshInboxEvent, row.id)
        # A process crash after claim_refresh_run's commit but before this
        # association update is recoverable by a subsequent delivery.  Find
        # the idempotent run and attach it rather than creating another run.
        row.run_id = run.id
        db.commit()
        db.refresh(row)
        db.refresh(run)
        return _receipt(db, row, status="received")

    def request_cancellation(self, db: Session, *, run_id: str, requested_by: str, reason: str, now: datetime) -> RefreshRun:
        return request_refresh_cancellation(db, run_id=run_id, requested_by=requested_by, reason=reason, now=_as_utc(now))

    def _dead_letter_event(self, db: Session, *, run_id: str, reason: str, now: datetime) -> RefreshRun:
        run = db.execute(select(RefreshRun).where(RefreshRun.id == run_id).with_for_update()).scalar_one()
        if run.status in {"succeeded", "failed", "dead_lettered", "cancelled"}:
            db.commit()
            return run
        inbox = db.execute(
            select(RefreshInboxEvent).where(RefreshInboxEvent.run_id == run.id).with_for_update()
        ).scalar_one_or_none()
        old_status = run.status
        run.status = "dead_lettered"
        run.retry_reason = reason
        run.terminal_at = now
        run.lease_owner = None
        run.lease_expires_at = None
        run.dispatch_state = "pending"
        letter = RefreshDeadLetter(
            id=str(uuid.uuid4()), source_id=run.source_id, resource=run.resource,
            event_id=inbox.event_id if inbox is not None else None, run_id=run.id,
            reason=reason,
            delivery_attempts=max(run.retry_count, inbox.delivery_attempts if inbox is not None else 0),
        )
        db.add(letter)
        if inbox is not None:
            inbox.state = "dead_lettered"
            inbox.delivery_attempts = max(inbox.delivery_attempts, run.retry_count)
        state = db.execute(
            select(RefreshSourceState).where(
                RefreshSourceState.source_id == run.source_id,
                RefreshSourceState.resource == run.resource,
            ).with_for_update()
        ).scalar_one_or_none()
        if state is not None and state.fencing_token == run.fencing_token:
            state.lease_owner = None
            state.lease_expires_at = None
            state.updated_at = now
        from app.models.v2.refresh import RefreshRunTransition

        db.add(RefreshRunTransition(
            id=str(uuid.uuid4()), run_id=run.id, from_status=old_status,
            to_status="dead_lettered", reason=reason,
        ))
        db.commit()
        db.refresh(run)
        return run

    def _dead_letter_inbox_event(
        self, db: Session, *, inbox_id: str, reason: str, now: datetime,
    ) -> RefreshDeadLetter:
        """Terminally retain a runless inbox event when its frozen envelope
        can no longer be admitted under the current source revision.

        No cursor or source lease is changed here: the accepted envelope and
        its reason remain available for an operator replay after reconciliation.
        """
        inbox = db.execute(
            select(RefreshInboxEvent).where(RefreshInboxEvent.id == inbox_id).with_for_update()
        ).scalar_one()
        letter = db.execute(
            select(RefreshDeadLetter).where(
                RefreshDeadLetter.source_id == inbox.source_id,
                RefreshDeadLetter.resource == inbox.resource,
                RefreshDeadLetter.event_id == inbox.event_id,
                RefreshDeadLetter.run_id.is_(None),
            ).with_for_update()
        ).scalars().first()
        if letter is None:
            letter = RefreshDeadLetter(
                id=str(uuid.uuid4()), source_id=inbox.source_id, resource=inbox.resource,
                event_id=inbox.event_id, reason=reason,
                delivery_attempts=inbox.delivery_attempts,
            )
            db.add(letter)
        inbox.state = "dead_lettered"
        inbox.processed_at = None
        db.commit()
        db.refresh(letter)
        return letter

    def dead_letter(self, db: Session, *, run_id: str, reason: str, now: datetime) -> RefreshRun:
        return self._dead_letter_event(db, run_id=run_id, reason=reason, now=_as_utc(now))

    def _failure(self, db: Session, *, run_id: str, reason: str, now: datetime):
        current = db.get(RefreshRun, run_id)
        if current is None:
            raise RefreshError("REFRESH_RUN_NOT_FOUND", f"no RefreshRun {run_id}")
        if current.retry_count + 1 >= self.max_attempts:
            current = self._dead_letter_event(db, run_id=run_id, reason=reason, now=now)
            return polling._result(db, current, error_code="DEAD_LETTERED")
        inbox = db.execute(select(RefreshInboxEvent).where(RefreshInboxEvent.run_id == run_id).with_for_update()).scalar_one_or_none()
        if inbox is not None:
            inbox.delivery_attempts += 1
        current = mark_refresh_retryable(db, run_id=run_id, reason=reason, now=now)
        return polling._result(db, current, error_code="RETRYABLE")

    def dispatch_pending(
        self,
        db: Session,
        *,
        run_id: str,
        dispatch: Callable[[str], object],
        now: datetime,
    ) -> IngestReceipt:
        """Publish one durable event run, recording broker outcome in SQL.

        The callback receives only the durable run ID.  A short SQL claim
        prevents concurrent drainers from publishing the same pending run;
        an expired claim is deliberately recoverable for at-least-once
        delivery after a dispatcher crash.
        """
        now = _as_utc(now)
        run = db.execute(
            select(RefreshRun).where(RefreshRun.id == run_id).with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise RefreshError("REFRESH_RUN_NOT_FOUND", f"no RefreshRun {run_id}")
        row = db.execute(
            select(RefreshInboxEvent).where(RefreshInboxEvent.run_id == run.id).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            db.rollback()
            raise RefreshError("INBOX_EVENT_NOT_FOUND", f"no durable inbox event for run {run_id}")
        if run.status in {"succeeded", "failed", "dead_lettered", "cancelled"}:
            db.commit()
            status = "dead_lettered" if run.status == "dead_lettered" else "processed" if run.status == "succeeded" else run.status
            return _receipt(db, row, status=status, reason_code="DEAD_LETTERED" if status == "dead_lettered" else "DUPLICATE_EVENT")

        retry_at = run.dispatch_retry_at
        if retry_at is not None and _as_utc(retry_at) > now:
            db.commit()
            return _receipt(db, row, status="pending", reason_code="PUBLISH_RETRY_WAIT")

        claim_owner = f"event-dispatcher:{uuid.uuid4()}"
        claim_expires_at = now + timedelta(seconds=60)
        existing_claim = run.dispatch_claim_expires_at
        if (
            run.dispatch_claim_owner is not None
            and existing_claim is not None
            and _as_utc(existing_claim) > now
        ):
            db.commit()
            return _receipt(db, row, status="pending", reason_code="PUBLISH_IN_FLIGHT")
        run.dispatch_claim_owner = claim_owner
        run.dispatch_claim_expires_at = claim_expires_at
        db.commit()

        try:
            # No event body, source URL, or credential crosses this callback.
            dispatch(run.id)
        except Exception:
            db.rollback()
            current = db.execute(
                select(RefreshRun).where(RefreshRun.id == run.id).with_for_update()
            ).scalar_one()
            current_row = db.execute(
                select(RefreshInboxEvent).where(RefreshInboxEvent.run_id == current.id).with_for_update()
            ).scalar_one()
            if current.dispatch_claim_owner == claim_owner:
                current_row.delivery_attempts += 1
                if current_row.delivery_attempts >= self.max_attempts:
                    # Flush the persisted attempt before the terminal helper
                    # commits the run/inbox/DLQ state together.
                    db.flush()
                    self._dead_letter_event(db, run_id=current.id, reason="PUBLISH_FAILED", now=now)
                    durable = db.get(RefreshInboxEvent, current_row.id)
                    return _receipt(
                        db, durable, status="dead_lettered", reason_code="PUBLISH_EXHAUSTED",
                    )
                current.dispatch_state = "publish_failed"
                current.dispatch_reason = "PUBLISH_FAILED"
                current.dispatch_retry_at = now + timedelta(
                    seconds=self._publish_retry_delay(current_row.delivery_attempts),
                )
                current.dispatch_claim_owner = None
                current.dispatch_claim_expires_at = None
            db.commit()
            return _receipt(db, current_row, status="publish_failed", reason_code="PUBLISH_FAILED")

        db.rollback()
        current = db.execute(
            select(RefreshRun).where(RefreshRun.id == run.id).with_for_update()
        ).scalar_one()
        current_row = db.execute(
            select(RefreshInboxEvent).where(RefreshInboxEvent.run_id == current.id).with_for_update()
        ).scalar_one()
        if current.dispatch_claim_owner != claim_owner:
            db.commit()
            return _receipt(db, current_row, status="pending", reason_code="PUBLISH_IN_FLIGHT")
        current.dispatch_state = "dispatched"
        current.dispatch_reason = None
        current.dispatch_retry_at = None
        current.dispatch_claim_owner = None
        current.dispatch_claim_expires_at = None
        db.commit()
        return _receipt(db, current_row, status="received")

    def drain_pending(
        self,
        db: Session,
        *,
        dispatch: Callable[[str], object],
        lease_owner: str,
        now: datetime,
        source_id: str | None = None,
        resource: str | None = None,
        limit: int = 100,
    ) -> list[IngestReceipt]:
        """Recover accepted inbox rows after lease/broker interruption.

        Rows are selected from SQL rather than an in-memory queue.  If the
        source lease is busy, the row remains `received` and is retried on a
        later drain; no request is allowed to turn that expected contention
        into a 500 response.
        """
        now = _as_utc(now)
        statement = select(RefreshInboxEvent).where(
            RefreshInboxEvent.state.in_(("received", "duplicate")),
            RefreshInboxEvent.envelope_json.is_not(None),
        )
        if source_id is not None:
            statement = statement.where(RefreshInboxEvent.source_id == source_id)
        if resource is not None:
            statement = statement.where(RefreshInboxEvent.resource == resource)
        rows = db.execute(
            statement.order_by(RefreshInboxEvent.created_at).limit(max(1, min(500, int(limit))))
        ).scalars().all()
        receipts: list[IngestReceipt] = []
        for candidate in rows:
            db.rollback()
            row = db.execute(
                select(RefreshInboxEvent).where(RefreshInboxEvent.id == candidate.id).with_for_update()
            ).scalar_one_or_none()
            if row is None or row.state not in {"received", "duplicate"} or not row.envelope_json:
                continue

            run = db.get(RefreshRun, row.run_id) if row.run_id else None
            if run is None:
                try:
                    envelope = _envelope_from_json(row.envelope_json, received_at=row.received_at)
                    state = db.execute(
                        select(RefreshSourceState).where(
                            RefreshSourceState.source_id == row.source_id,
                            RefreshSourceState.resource == row.resource,
                        ).with_for_update()
                    ).scalar_one_or_none()
                    if state is None:
                        db.rollback()
                        continue
                    self._validate_source_contract(db, state=state, envelope=envelope)
                except (EventIngressError, RefreshError) as exc:
                    # The original acceptance remains durable and auditable;
                    # a current config/schema change must not erase it.  Move
                    # the runless row to the durable DLQ so an operator can
                    # reconcile the source and replay the stored envelope.
                    reason_code = getattr(exc, "reason_code", "SCHEMA_DRIFT")
                    db.rollback()
                    letter = self._dead_letter_inbox_event(
                        db, inbox_id=row.id, reason=reason_code, now=now,
                    )
                    durable = db.get(RefreshInboxEvent, row.id)
                    receipts.append(_receipt(db, durable, status="dead_lettered", reason_code=reason_code))
                    continue

                existing = db.execute(
                    select(RefreshRun).where(
                        RefreshRun.source_id == row.source_id,
                        RefreshRun.resource == row.resource,
                        RefreshRun.idempotency_key == _event_idempotency_key(
                            source_id=row.source_id, resource=row.resource, event_id=row.event_id,
                        ),
                    ).order_by(RefreshRun.created_at.desc())
                ).scalars().first()
                try:
                    run = existing or claim_refresh_run(
                        db, source_id=row.source_id, resource=row.resource,
                        policy=RefreshPolicy.EVENT_DRIVEN,
                        idempotency_key=_event_idempotency_key(
                            source_id=row.source_id, resource=row.resource, event_id=row.event_id,
                        ),
                        lease_owner=lease_owner, now=now, default_cursor_contract=state.cursor_contract,
                    )
                except RefreshLeaseError:
                    db.rollback()
                    continue
                run.trigger = "event"
                run.dispatch_queue = QUEUE_REFRESH_EVENT
                row.run_id = run.id
                db.commit()
            if run.dispatch_state != "dispatched":
                receipts.append(self.dispatch_pending(db, run_id=run.id, dispatch=dispatch, now=now))
            else:
                receipts.append(_receipt(db, row, status="received"))
        return receipts

    def process(self, db: Session, *, run_id: str, lease_owner: str | None = None, now: datetime) -> polling.RefreshRunResult:
        """Materialize one accepted inbox row under its frozen run contract."""
        now = _as_utc(now)
        run = db.get(RefreshRun, run_id)
        if run is None:
            raise RefreshError("REFRESH_RUN_NOT_FOUND", f"no RefreshRun {run_id}")
        if run.status in {"succeeded", "failed", "dead_lettered", "cancelled"}:
            return polling._result(db, run)
        if run.status == "queued":
            run = polling._claim_queued_run(
                db, run_id=run.id, lease_owner=lease_owner or f"refresh-event-worker:{uuid.uuid4()}", now=now,
            )
            if run.status in {"succeeded", "failed", "dead_lettered", "cancelled"}:
                return polling._result(db, run)
        inbox = db.execute(select(RefreshInboxEvent).where(RefreshInboxEvent.run_id == run.id)).scalar_one_or_none()
        if inbox is None or not inbox.envelope_json:
            raise RefreshError("INBOX_EVENT_NOT_FOUND", f"no durable inbox event for run {run_id}")
        owner = run.lease_owner or lease_owner
        if not owner:
            raise RefreshLeaseError("REFRESH_LEASE_MISSING", "event run has no lease owner")
        if run.status == "cancel_requested":
            try:
                cancelled = finalize_refresh_cancellation(
                    db, run_id=run.id, lease_owner=owner, fencing_token=run.fencing_token, now=now,
                )
            except RefreshError:
                cancelled = db.get(RefreshRun, run.id)
            return polling._result(db, cancelled)

        try:
            envelope = _envelope_from_json(inbox.envelope_json, received_at=inbox.received_at)
            polling._assert_configuration_current(db, run)
            assert_refresh_not_cancelled(
                db, run_id=run.id, lease_owner=owner, fencing_token=run.fencing_token, now=now,
            )
            before = run.cursor_before
            candidate = polling._event_cursor(envelope, run.cursor_contract)
            next_cursor = polling._max_cursor(before, candidate)
            out_of_order = before is not None and candidate is not None and polling._safe_cursor_compare(candidate, before) <= 0
            provenance = {
                "refresh_run_id": run.id,
                "source_id": run.source_id,
                "resource": run.resource,
                "event_id": envelope.event_id,
                "event_source": "managed_webhook_or_outbox",
                "config_version": run.config_version,
                "cursor_contract": run.cursor_contract,
                "cursor_outcome": "unchanged" if out_of_order or next_cursor == before else "advanced",
                "source_observed_at": envelope.occurred_at.isoformat() if envelope.occurred_at else None,
            }
            run.duplicate_count = 0
            run.late_count = 1 if out_of_order else 0
            run.lag_seconds = max(0, int((now - _as_utc(envelope.occurred_at)).total_seconds())) if envelope.occurred_at else 0
            run.source_provenance = provenance
            db.flush()

            # This is the same tentative-materialization boundary used by
            # polling; the fenced outcome check below remains the final guard.
            polling._assert_configuration_current(db, run)
            assert_refresh_not_cancelled(
                db, run_id=run.id, lease_owner=owner, fencing_token=run.fencing_token, now=now,
            )
            dataset = polling._resolve_dataset(db, source_id=run.source_id, resource=run.resource, connection=None)
            pipeline = polling._resolve_pipeline(db, dataset=dataset)
            from app.services.v2.dataset_service import DatasetService
            from app.models.v2.pipeline import PipelineRun

            version = DatasetService(db).create_version(
                dataset.id,
                polling._serialize_envelopes([envelope]),
                rowcount=1,
                refresh_run_id=run.id,
                source_cursor=next_cursor,
                observed_at=envelope.occurred_at,
                commit=False,
            )
            pipeline_run = PipelineRun(
                id=str(uuid.uuid4()), pipeline_id=pipeline.id, status="success",
                started_at=now, finished_at=now, dataset_version_id=version.id,
                stats={"refresh_run_id": run.id, "rows_in": 1, "duplicate_count": 0, "late_event_count": run.late_count},
            )
            db.add(pipeline_run)
            db.flush()
            completed = record_refresh_outcome(
                db, run_id=run.id, lease_owner=owner, fencing_token=run.fencing_token,
                config_version=run.config_version, cursor_contract=run.cursor_contract,
                input_dataset_version_ids=[version.id], pipeline_run_id=pipeline_run.id,
                next_cursor=next_cursor,
                quality_summary={"rows": 1, "duplicates": 0, "late": run.late_count},
                provenance=provenance, now=now,
                input_source_cursor=next_cursor, input_provenance=provenance, commit=False,
            )
            inbox = db.execute(select(RefreshInboxEvent).where(RefreshInboxEvent.id == inbox.id).with_for_update()).scalar_one()
            inbox.state = "processed"
            inbox.delivery_attempts += 1
            inbox.processed_at = now
            db.commit()
            db.refresh(completed)
            return polling._result(db, completed, source_lag_seconds=completed.lag_seconds)
        except RefreshCancellationRequested:
            db.rollback()
            current = db.get(RefreshRun, run.id)
            try:
                finalized = finalize_refresh_cancellation(
                    db, run_id=run.id, lease_owner=current.lease_owner or owner,
                    fencing_token=current.fencing_token, now=now,
                )
            except RefreshError:
                finalized = db.get(RefreshRun, run.id)
            return polling._result(db, finalized)
        except ConfigurationDriftError:
            db.rollback()
            raise
        except (RefreshFencingError, RefreshLeaseError):
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            return self._failure(db, run_id=run.id, reason=str(exc), now=now)

    def replay_dead_letter(
        self, db: Session, *, dead_letter_id: str, operator_id: str, now: datetime,
    ) -> RefreshRun:
        now = _as_utc(now)
        letter = db.execute(
            select(RefreshDeadLetter).where(RefreshDeadLetter.id == dead_letter_id).with_for_update()
        ).scalar_one_or_none()
        if letter is None:
            raise RefreshError("DEAD_LETTER_NOT_FOUND", f"no dead letter {dead_letter_id}")
        old = db.get(RefreshRun, letter.run_id) if letter.run_id else None
        source_id = old.source_id if old is not None else letter.source_id
        resource = old.resource if old is not None else letter.resource
        replay_key = f"replay:event:{letter.id}"
        existing = db.execute(
            select(RefreshRun).where(
                RefreshRun.source_id == source_id,
                RefreshRun.resource == resource,
                RefreshRun.idempotency_key == replay_key,
            ).order_by(RefreshRun.created_at.desc())
        ).scalars().first()
        if letter.replay_status != "pending":
            if letter.replay_run_id:
                linked = db.get(RefreshRun, letter.replay_run_id)
                if linked is not None:
                    db.commit()
                    return linked
            if existing is not None:
                letter.replay_run_id = existing.id
                db.commit()
                return existing
            db.rollback()
            raise RefreshError("REPLAY_NOT_ALLOWED", "dead letter has already been replayed or discarded")
        inbox = db.execute(
            select(RefreshInboxEvent).where(
                RefreshInboxEvent.source_id == letter.source_id,
                RefreshInboxEvent.resource == letter.resource,
                RefreshInboxEvent.event_id == letter.event_id,
            ).with_for_update()
        ).scalar_one_or_none()
        if inbox is None or not inbox.envelope_json:
            raise RefreshError("INBOX_EVENT_NOT_FOUND", "dead letter has no stored envelope")
        # The idempotency key is derived solely from the immutable DLQ ID.  If
        # a process crashed after claim_refresh_run committed but before the
        # replay audit update, this lookup recovers the same run.
        if old is None:
            state = db.execute(
                select(RefreshSourceState).where(
                    RefreshSourceState.source_id == source_id,
                    RefreshSourceState.resource == resource,
                )
            ).scalar_one_or_none()
            if state is None:
                db.rollback()
                raise RefreshError("SOURCE_STATE_MISSING", "dead letter source has no current state")
            policy = RefreshPolicy.EVENT_DRIVEN
            default_cursor_contract = state.cursor_contract
        else:
            policy = old.policy
            default_cursor_contract = old.cursor_contract
        new = existing or claim_refresh_run(
            db, source_id=source_id, resource=resource, policy=policy,
            idempotency_key=replay_key, lease_owner=f"{operator_id}:replay",
            now=now, default_cursor_contract=default_cursor_contract,
        )
        new.trigger = "replay"
        new.dispatch_queue = QUEUE_REFRESH_EVENT
        inbox.state = "received"
        inbox.delivery_attempts = 0
        inbox.processed_at = None
        inbox.run_id = new.id
        letter.replay_status = "replayed"
        letter.replayed_at = now
        letter.replayed_by = operator_id
        letter.replay_run_id = new.id
        db.commit()
        db.refresh(new)
        return new


def replay_dead_letter(
    db: Session, *, dead_letter_id: str, operator_id: str, now: datetime,
) -> RefreshRun:
    return EventIngestService().replay_dead_letter(
        db, dead_letter_id=dead_letter_id, operator_id=operator_id, now=now,
    )


__all__ = ["EventIngestService", "IngestReceipt", "replay_dead_letter"]
