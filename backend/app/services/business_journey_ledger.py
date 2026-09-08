"""Atomic application-owned model-call slots for business journeys."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.business_journey import (
    PREPARATION_SLOT_SCOPE,
    BusinessJourneyModelCall,
    BusinessJourneyPreparation,
)


class JourneyLedgerError(Exception):
    pass


def record_preparation_call(db: Session, *, run_id: str, journey_id: str, call: dict,
                            model_config_version_id: str) -> BusinessJourneyModelCall:
    return _create_slot(
        db, run_id=run_id, journey_id=journey_id, turn_id=PREPARATION_SLOT_SCOPE,
        logical_call_index=1,
        phase="preparation", status="finalized", call_kind=str(call["call_kind"]),
        correlation_id=str(call["correlation_id"]), model_config_version_id=model_config_version_id,
        requested_model=str(call["requested_model"]), observed_model=str(call["observed_model"]),
        http_attempts=int(call["http_attempts"]), retry_count=int(call["retry_count"]),
    )


def reserve_runtime_call(db: Session, *, run_id: str, journey_id: str, turn_id: str,
                         logical_call_index: int,
                         call_kind: str, correlation_id: str, model_config_version_id: str) -> BusinessJourneyModelCall:
    preparation = db.query(BusinessJourneyPreparation).filter_by(
        run_id=run_id, journey_id=journey_id,
    ).first()
    if preparation is None:
        raise JourneyLedgerError("PREPARATION_BINDING_MISSING")
    if preparation.model_config_version_id != model_config_version_id:
        raise JourneyLedgerError("MODEL_CONFIG_VERSION_MISMATCH")
    return _create_slot(
        db, run_id=run_id, journey_id=journey_id, turn_id=turn_id,
        logical_call_index=logical_call_index,
        phase="runtime", status="reserved", call_kind=call_kind, correlation_id=correlation_id,
        model_config_version_id=model_config_version_id,
    )


def finalize_runtime_call(slot: BusinessJourneyModelCall, *, observed_model: str,
                          requested_model: str, http_attempts: int, retry_count: int) -> None:
    slot.status = "finalized"
    slot.observed_model = observed_model
    slot.requested_model = requested_model
    slot.http_attempts = http_attempts
    slot.retry_count = retry_count
    slot.finalized_at = datetime.now(timezone.utc)


def fail_runtime_call(slot: BusinessJourneyModelCall) -> None:
    slot.status = "failed"
    slot.finalized_at = datetime.now(timezone.utc)


def _create_slot(db: Session, **values) -> BusinessJourneyModelCall:
    existing = db.query(BusinessJourneyModelCall).filter_by(
        run_id=values["run_id"], journey_id=values["journey_id"],
        turn_id=values["turn_id"],
        logical_call_index=values["logical_call_index"],
    ).first()
    if existing is not None:
        raise JourneyLedgerError("MODEL_CALL_SLOT_ALREADY_EXISTS")
    slot = BusinessJourneyModelCall(**values)
    try:
        with db.begin_nested():
            db.add(slot)
            db.flush()
    except IntegrityError as exc:
        raise JourneyLedgerError("MODEL_CALL_SLOT_ALREADY_EXISTS") from exc
    return slot
