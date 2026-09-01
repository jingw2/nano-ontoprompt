"""Durable source refresh contract — state machine (Task 6).

Owns every write to `RefreshSourceState`/`RefreshRun`: authoritative source
configuration revisions, fenced claim/outcome, best-effort cancellation, and
retry interruption. Every mutating function here locks the authoritative
`RefreshSourceState` (and, where relevant, the `RefreshRun`) with
`SELECT ... FOR UPDATE` before reading or writing so PostgreSQL serializes
concurrent workers on the same (source_id, resource); the ORM-level
`.with_for_update()` degrades to a no-op on SQLite, which is only ever used
by this module's non-concurrent unit tests.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.v2.refresh import (
    RefreshRun,
    RefreshRunTransition,
    RefreshSourceState,
)
from app.models.v2.pipeline import PipelineRunInput
from app.schemas.refresh import (
    ConfigurationDriftError,
    RefreshCancellationRequested,
    RefreshError,
    RefreshFencingError,
    RefreshLeaseError,
    RefreshPolicy,
    SourceCursor,
    cursor_order,  # noqa: F401  (re-exported for callers of this module)
)

ACTIVE_RUN_STATUSES = ("queued", "running")


def _new_id() -> str:
    return str(uuid.uuid4())


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize dialects (SQLite/MySQL may return naive datetimes)."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _cursor_to_json(cursor: SourceCursor | None) -> dict | None:
    if cursor is None:
        return None
    return {
        "watermark": cursor.watermark,
        "primary_key": cursor.primary_key,
        "opaque_value": cursor.opaque_value,
        "observed_at": cursor.observed_at.isoformat() if cursor.observed_at else None,
    }


def _record_transition(db: Session, *, run: RefreshRun, from_status: str | None, to_status: str,
                        reason: str | None = None, actor: str | None = None) -> None:
    db.add(RefreshRunTransition(
        id=_new_id(), run_id=run.id, from_status=from_status, to_status=to_status,
        reason=reason, actor=actor,
    ))


def _lock_source_state(db: Session, *, source_id: str, resource: str) -> RefreshSourceState | None:
    return db.execute(
        select(RefreshSourceState)
        .where(RefreshSourceState.source_id == source_id, RefreshSourceState.resource == resource)
        .with_for_update()
    ).scalar_one_or_none()


def _lock_or_create_source_state(
    db: Session, *, source_id: str, resource: str, now: datetime,
    default_cursor_contract: str = "watermark_primary_key",
) -> RefreshSourceState:
    state = _lock_source_state(db, source_id=source_id, resource=resource)
    if state is not None:
        return state
    # First claim for a brand-new (source_id, resource): FOR UPDATE cannot
    # lock a row that does not exist yet. Race a plain (dialect-portable)
    # insert inside a SAVEPOINT; a concurrent first claim on another
    # connection may win first and violate the (source_id, resource) unique
    # constraint — recover by rolling back just the failed insert and
    # re-locking the row the winner committed, instead of relying on a
    # Postgres-only ON CONFLICT upsert.
    savepoint = db.begin_nested()
    try:
        db.add(RefreshSourceState(
            id=_new_id(), source_id=source_id, resource=resource,
            cursor_contract=default_cursor_contract, config_version=1, fencing_token=0,
            created_at=now, updated_at=now,
        ))
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
    state = _lock_source_state(db, source_id=source_id, resource=resource)
    if state is None:  # pragma: no cover - defensive; the losing branch above always leaves a winner's row behind
        raise RefreshError("SOURCE_STATE_MISSING", "failed to provision RefreshSourceState")
    return state


def _lock_run(db: Session, *, run_id: str) -> RefreshRun:
    # populate_existing=True: without it, a RefreshRun already loaded into
    # this session's identity map (e.g. flushed earlier in the same
    # transaction) is returned as-is, ignoring the row FOR UPDATE just
    # locked and re-read from the database. Concretely: an operator's
    # cancellation commits `status='cancel_requested'` after this worker's
    # session already loaded the run, and this call returns the stale
    # pre-cancellation instance instead of the row it just locked, letting
    # record_refresh_outcome commit the run as succeeded/failed with the
    # cancellation silently ignored.
    run = db.execute(
        select(RefreshRun).where(RefreshRun.id == run_id).with_for_update(),
        execution_options={"populate_existing": True},
    ).scalar_one_or_none()
    if run is None:
        db.rollback()
        raise RefreshError("REFRESH_RUN_NOT_FOUND", f"no RefreshRun {run_id}")
    return run


def update_source_configuration(
    db: Session, *, source_id: str, resource: str, cursor_contract: str,
    configuration: Mapping[str, object], now: datetime,
) -> RefreshSourceState:
    """Atomically bump the source configuration revision.

    Increments `config_version`, stores the new non-secret configuration and
    cursor contract, clears the active lease, and increments the fencing
    token so every claim issued against the prior revision is invalid. The
    new revision is the only revision eligible for future claims.
    """
    state = _lock_or_create_source_state(
        db, source_id=source_id, resource=resource, now=now, default_cursor_contract=cursor_contract,
    )
    state.config_version += 1
    state.cursor_contract = cursor_contract
    state.configuration = dict(configuration)
    state.lease_owner = None
    state.lease_expires_at = None
    state.fencing_token += 1
    state.updated_at = now
    db.commit()
    db.refresh(state)
    return state


def claim_refresh_run(
    db: Session, *, source_id: str, resource: str, policy: RefreshPolicy | str,
    idempotency_key: str, lease_owner: str, now: datetime, lease_seconds: int = 300,
    default_cursor_contract: str = "watermark_primary_key",
) -> RefreshRun:
    """Claim (or idempotently reuse) a refresh run for (source_id, resource).

    Locks the authoritative RefreshSourceState with SELECT ... FOR UPDATE.
    `lease_expires_at > now` is treated as an active lease: a claim for the
    same owner/idempotency_key/config_version reuses the still-active run;
    any other claim while a lease is active is rejected with
    RefreshLeaseError. A new claim copies the current config_version and
    cursor_contract into the run and increments the state's fencing token.
    """
    state = _lock_or_create_source_state(
        db,
        source_id=source_id,
        resource=resource,
        now=now,
        default_cursor_contract=default_cursor_contract,
    )
    policy_value = policy.value if isinstance(policy, RefreshPolicy) else policy

    active = _as_utc(state.lease_expires_at) is not None and _as_utc(state.lease_expires_at) > now
    if active:
        existing = db.execute(
            select(RefreshRun).where(
                RefreshRun.source_id == source_id,
                RefreshRun.resource == resource,
                RefreshRun.config_version == state.config_version,
                RefreshRun.idempotency_key == idempotency_key,
                RefreshRun.lease_owner == lease_owner,
                RefreshRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        ).scalar_one_or_none()
        if existing is not None:
            db.commit()
            return existing
        db.rollback()
        raise RefreshLeaseError("REFRESH_LEASE_ACTIVE", "another lease is currently active for this source/resource")

    new_fencing_token = state.fencing_token + 1
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    run = RefreshRun(
        id=_new_id(), source_id=source_id, resource=resource, policy=policy_value,
        config_version=state.config_version, cursor_contract=state.cursor_contract,
        status="running", dispatch_state="pending", idempotency_key=idempotency_key,
        lease_owner=lease_owner, lease_expires_at=lease_expires_at,
        fencing_token=new_fencing_token, retry_count=0,
        cursor_before_json=state.cursor_json,
    )
    db.add(run)
    _record_transition(db, run=run, from_status=None, to_status="running", actor=lease_owner)

    state.fencing_token = new_fencing_token
    state.lease_owner = lease_owner
    state.lease_expires_at = lease_expires_at
    state.updated_at = now

    db.commit()
    db.refresh(run)
    return run


def record_refresh_outcome(
    db: Session, *, run_id: str, lease_owner: str, fencing_token: int, config_version: int,
    cursor_contract: str, input_dataset_version_ids: Sequence[str], pipeline_run_id: str,
    next_cursor: SourceCursor | None, quality_summary: Mapping[str, object], provenance: Mapping[str, object],
    now: datetime, input_source_cursor: SourceCursor | None = None,
    input_provenance: Mapping[str, object] | None = None, commit: bool = True,
) -> RefreshRun:
    """Commit a successful refresh outcome, or reject it without side effects.

    Configuration drift (frozen config_version/cursor_contract no longer
    matching the run or the authoritative source state) is checked FIRST,
    before any lease/fencing/cancellation check, and rolls back with no
    lineage or cursor mutation. Only once that matches are lease expiry,
    exact lease owner, and the source fencing token validated (typed
    lease/fencing errors), then the run's cancellation state (rolled back on
    RefreshCancellationRequested), and only then are the PipelineRunInput
    associations written and the cursor CAS'd forward in the same
    transaction that flips status to "succeeded".
    """
    run = _lock_run(db, run_id=run_id)
    state = _lock_source_state(db, source_id=run.source_id, resource=run.resource)
    if state is None:  # pragma: no cover - defensive; a claimed run always has a state row
        db.rollback()
        raise RefreshError("SOURCE_STATE_MISSING", "no RefreshSourceState for this run")

    # 1. Configuration drift — checked before anything else, and before any
    #    lineage/cursor mutation is even staged.
    if (
        config_version != run.config_version
        or cursor_contract != run.cursor_contract
        or config_version != state.config_version
        or cursor_contract != state.cursor_contract
    ):
        db.rollback()
        raise ConfigurationDriftError("frozen config_version/cursor_contract no longer matches the source revision")

    # 2. Lease/fencing — only reached once the revision matches.
    if fencing_token != state.fencing_token:
        db.rollback()
        raise RefreshFencingError("REFRESH_FENCING_STALE", "fencing token no longer matches the source state")
    if state.lease_owner != lease_owner:
        db.rollback()
        raise RefreshFencingError("REFRESH_FENCING_OWNER_MISMATCH", "lease owner no longer matches the source state")
    if _as_utc(state.lease_expires_at) is None or _as_utc(state.lease_expires_at) <= now:
        db.rollback()
        raise RefreshLeaseError("REFRESH_LEASE_EXPIRED", "lease has expired")

    # 3. Cancellation guard, held under the same lock (see module docstring
    #    for why this is inlined rather than delegated to a nested
    #    assert_refresh_not_cancelled call that would commit mid-transaction).
    if run.status == "cancel_requested":
        db.rollback()
        raise RefreshCancellationRequested("REFRESH_CANCELLATION_REQUESTED", "run has an in-flight cancellation request")

    # Stage the lineage associations and outcome fields.
    for ordinal, dataset_version_id in enumerate(input_dataset_version_ids):
        db.add(PipelineRunInput(
            id=_new_id(), pipeline_run_id=pipeline_run_id, dataset_version_id=dataset_version_id,
            input_ordinal=ordinal,
            source_cursor=_cursor_to_json(input_source_cursor),
            provenance=dict(input_provenance) if input_provenance is not None else None,
        ))
    run.pipeline_run_id = pipeline_run_id
    run.source_provenance = dict(provenance)
    run.quality_summary = dict(quality_summary)
    run.cursor_after_json = _cursor_to_json(next_cursor)

    # Re-check cancellation immediately before the cursor CAS — still under
    # the same row locks acquired above, so this observes no new state.
    db.flush()
    if run.status == "cancel_requested":
        db.rollback()
        raise RefreshCancellationRequested("REFRESH_CANCELLATION_REQUESTED", "cancelled before the outcome committed")

    state.cursor_contract = cursor_contract
    state.cursor_json = _cursor_to_json(next_cursor)
    state.lease_owner = None
    state.lease_expires_at = None
    state.last_successful_run_id = run.id
    state.updated_at = now

    _record_transition(db, run=run, from_status=run.status, to_status="succeeded", actor=lease_owner)
    run.status = "succeeded"
    run.terminal_at = now

    if commit:
        db.commit()
        db.refresh(run)
    else:
        # Event ingestion extends this fenced transaction with the durable
        # inbox state transition, so DatasetVersion/PipelineRun, cursor, run,
        # and inbox are committed atomically by the caller.
        db.flush()
    return run


def _check_not_cancelled(run: RefreshRun) -> None:
    if run.status == "cancel_requested":
        raise RefreshCancellationRequested("REFRESH_CANCELLATION_REQUESTED", "run has an in-flight cancellation request")


def assert_refresh_not_cancelled(db: Session, *, run_id: str, lease_owner: str, fencing_token: int, now: datetime) -> None:
    """Standalone cancellation-safe-point guard for connector/worker code.

    Locks the run and authoritative source state, verifies the presented
    owner/fence/configuration are still current, and raises
    RefreshCancellationRequested if the run has an in-flight cancellation
    request. Manages its own transaction boundary (commits on a passing
    check, rolls back and raises otherwise) — callers that already hold the
    lock in a larger transaction (record_refresh_outcome) inline the
    equivalent check instead of calling this, to avoid a premature commit.
    """
    run = _lock_run(db, run_id=run_id)
    state = _lock_source_state(db, source_id=run.source_id, resource=run.resource)
    if state is None:  # pragma: no cover - defensive
        db.rollback()
        raise RefreshError("SOURCE_STATE_MISSING", "no RefreshSourceState for this run")
    if fencing_token != state.fencing_token or state.lease_owner != lease_owner:
        db.rollback()
        raise RefreshFencingError("REFRESH_FENCING_STALE", "fencing token/owner no longer matches the source state")
    if run.config_version != state.config_version or run.cursor_contract != state.cursor_contract:
        db.rollback()
        raise ConfigurationDriftError("frozen config_version/cursor_contract no longer matches the source revision")
    try:
        _check_not_cancelled(run)
    except RefreshCancellationRequested:
        db.rollback()
        raise
    db.commit()


def request_refresh_cancellation(db: Session, *, run_id: str, requested_by: str, reason: str, now: datetime) -> RefreshRun:
    """The only cancellation entry point (Milestone 2/3 Scope Amendment).

    `queued` -> terminal `cancelled` directly. An actively-leased `running`
    run additionally validates the current lease/fencing token and moves to
    `cancel_requested`, recording actor/reason/fencing snapshot; a `running`
    run whose lease/fence no longer matches the authoritative state (e.g.
    invalidated by a configuration revision) has no reachable worker to
    signal and is cancelled directly instead. `cancel_requested` returns the
    same durable request unchanged. Any already-terminal run (succeeded,
    failed, dead_lettered, cancelled) is returned unchanged with
    `already_terminal=True` — a plain result, never a typed error, and never
    reported as a successful cancellation.
    """
    if not requested_by:
        raise RefreshError("REFRESH_CANCELLATION_UNAUTHORIZED", "requested_by is required")

    run = _lock_run(db, run_id=run_id)
    state = _lock_source_state(db, source_id=run.source_id, resource=run.resource)

    if run.status == "queued":
        _record_transition(db, run=run, from_status="queued", to_status="cancelled", reason=reason, actor=requested_by)
        run.status = "cancelled"
        run.cancel_requested_at = now
        run.cancel_requested_by = requested_by
        run.cancel_reason = reason
        run.terminal_at = now
        run.already_terminal = False
        db.commit()
        db.refresh(run)
        return run

    if run.status == "running":
        fence_current = (
            state is not None
            and state.fencing_token == run.fencing_token
            and state.lease_owner == run.lease_owner
            and state.lease_expires_at is not None
            and _as_utc(state.lease_expires_at) > now
        )
        if fence_current:
            _record_transition(db, run=run, from_status="running", to_status="cancel_requested", reason=reason, actor=requested_by)
            run.status = "cancel_requested"
            run.cancel_requested_at = now
            run.cancel_requested_by = requested_by
            run.cancel_reason = reason
            run.cancel_fencing_token = run.fencing_token
            run.already_terminal = False
        else:
            # The lease has already been superseded/invalidated (e.g. by a
            # configuration revision) — there is no reachable worker to
            # signal, so cancel directly rather than waiting on a
            # safe-point that will never arrive.
            _record_transition(db, run=run, from_status="running", to_status="cancelled", reason=reason, actor=requested_by)
            run.status = "cancelled"
            run.cancel_requested_at = now
            run.cancel_requested_by = requested_by
            run.cancel_reason = reason
            run.terminal_at = now
            run.already_terminal = False
        db.commit()
        db.refresh(run)
        return run

    if run.status == "cancel_requested":
        run.already_terminal = False
        db.commit()
        db.refresh(run)
        return run

    # Any already-terminal status: plain result, no mutation.
    run.already_terminal = True
    db.commit()
    return run


def finalize_refresh_cancellation(db: Session, *, run_id: str, lease_owner: str, fencing_token: int, now: datetime) -> RefreshRun:
    """Transition `cancel_requested` -> terminal `cancelled`.

    Checks the requested `cancel_fencing_token` and the active owner/fence,
    clears the lease, and records the terminal time. Idempotent for an
    already-`cancelled` run; rejects a stale worker with RefreshFencingError.
    Never advances the source cursor, DatasetVersion/PipelineRun lineage, or
    snapshot state.
    """
    run = _lock_run(db, run_id=run_id)
    if run.status == "cancelled":
        db.commit()
        return run
    if run.status != "cancel_requested":
        db.rollback()
        raise RefreshFencingError("REFRESH_NOT_CANCEL_REQUESTED", "run is not awaiting a cancellation safe point")

    state = _lock_source_state(db, source_id=run.source_id, resource=run.resource)
    if (
        run.cancel_fencing_token != fencing_token
        or state is None
        or state.lease_owner != lease_owner
        or state.fencing_token != fencing_token
    ):
        db.rollback()
        raise RefreshFencingError("REFRESH_FENCING_STALE", "stale worker cannot finalize this cancellation")

    _record_transition(db, run=run, from_status="cancel_requested", to_status="cancelled", actor=lease_owner)
    run.status = "cancelled"
    run.terminal_at = now
    state.lease_owner = None
    state.lease_expires_at = None
    state.updated_at = now

    db.commit()
    db.refresh(run)
    return run


def mark_refresh_retryable(db: Session, *, run_id: str, reason: str, now: datetime) -> RefreshRun:
    """Idempotent durable `running -> queued` interruption transition.

    Increments retry_count, records the non-secret reason, clears the
    expired worker lease on the run, and sets dispatch_state="pending".
    Cursor-before/after and DatasetVersion/PipelineRun lineage are left
    unchanged. A terminal run is returned unchanged; this never replays a
    connector inline.
    """
    run = _lock_run(db, run_id=run_id)
    if run.status != "running":
        db.commit()
        return run

    previous_owner = run.lease_owner
    previous_fence = run.fencing_token
    state = _lock_source_state(db, source_id=run.source_id, resource=run.resource)

    _record_transition(db, run=run, from_status="running", to_status="queued", reason=reason)
    run.status = "queued"
    run.retry_count += 1
    run.retry_reason = reason
    run.dispatch_state = "pending"
    run.lease_owner = None
    run.lease_expires_at = None

    # The run and source lease are one claim.  Clear both while the source
    # row is locked; otherwise a retry would leave an orphaned source lease
    # that blocks every subsequent claim until expiry.
    if (
        state is not None
        and state.fencing_token == previous_fence
        and state.lease_owner == previous_owner
    ):
        state.lease_owner = None
        state.lease_expires_at = None
        state.updated_at = now

    db.commit()
    db.refresh(run)
    return run
