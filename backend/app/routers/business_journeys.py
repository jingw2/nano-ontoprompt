"""Narrow durable evidence API used by the real-model journey gate."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
import uuid

from app.deps import get_current_user, get_db, require_editor
from app.models.business_journey import BusinessJourneyPreparation
from app.models.user import User
from app.services.runtime.snapshots import SnapshotValidationError, materialize_snapshot
from app.models.v2.pipeline import PipelineRunInput

router = APIRouter()


class ModelCallEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    call_kind: str
    logical_call_index: int
    correlation_id: str
    requested_model: str
    observed_model: str
    http_attempts: int
    retry_count: int


class PreparationEvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    run_id: str
    journey_id: str
    ontology_id: str
    ontology_release_id: str
    pipeline_run_id: str
    dataset_version_id: str
    curated_dataset_id: str
    curated_review_id: str
    model_config_version_id: str
    structured: dict[str, Any]
    model_probe: dict[str, str]
    model_calls: list[ModelCallEvidence] = Field(min_length=1, max_length=1)


def _validate_model_evidence(body: PreparationEvidenceIn) -> None:
    call = body.model_calls[0]
    expected = f"{body.run_id}:{body.journey_id}:ontology:1"
    if (call.call_kind != "ontology" or call.logical_call_index != 1
            or call.correlation_id != expected or call.requested_model != "deepseek-v4-flash-vision-exp"
            or call.observed_model != "deepseek-v4-flash-vision-exp" or call.http_attempts not in (1, 2)):
        raise HTTPException(status_code=422, detail="PREPARATION_MODEL_EVIDENCE_INVALID")
    if body.model_probe.get("requested_model") != "deepseek-v4-flash-vision-exp" or body.model_probe.get("observed_model") != "deepseek-v4-flash-vision-exp":
        raise HTTPException(status_code=422, detail="PREPARATION_MODEL_PROBE_INVALID")


@router.post("/preparations", status_code=201)
def create_preparation(
    body: PreparationEvidenceIn, db: Session = Depends(get_db), current_user: User = Depends(require_editor),
):
    _validate_model_evidence(body)
    if db.query(BusinessJourneyPreparation).filter_by(run_id=body.run_id, journey_id=body.journey_id).first():
        raise HTTPException(status_code=409, detail="PREPARATION_EVIDENCE_EXISTS")
    # `PipelineRun.dataset_version_id` is the completed output pointer.  Make
    # that output an explicit governed association before snapshot creation;
    # retain every existing source association too, so the snapshot service's
    # complete-lineage rule remains authoritative rather than guessed here.
    inputs = db.query(PipelineRunInput).filter_by(pipeline_run_id=body.pipeline_run_id).all()
    if body.dataset_version_id not in {item.dataset_version_id for item in inputs}:
        next_ordinal = max((item.input_ordinal for item in inputs), default=-1) + 1
        db.add(PipelineRunInput(
            id=str(uuid.uuid4()), pipeline_run_id=body.pipeline_run_id,
            dataset_version_id=body.dataset_version_id, input_ordinal=next_ordinal,
            provenance={"business_journey_output": True},
        ))
        db.flush()
        inputs = db.query(PipelineRunInput).filter_by(pipeline_run_id=body.pipeline_run_id).all()
    try:
        snapshot = materialize_snapshot(
            db, ontology_release_id=body.ontology_release_id,
            dataset_version_ids=tuple(item.dataset_version_id for item in inputs), created_by=current_user.id,
        )
    except SnapshotValidationError as exc:
        raise HTTPException(status_code=422, detail=f"SEMANTIC_SNAPSHOT_MATERIALIZATION_FAILED:{exc.reason_code}") from exc
    row = BusinessJourneyPreparation(
        **body.model_dump(exclude={"model_calls"}), semantic_snapshot_id=snapshot.id,
        model_calls=[call.model_dump() for call in body.model_calls],
    )
    db.add(row)
    db.commit()
    return {"data": _serialize(row)}


@router.get("/preparations/{run_id}/{journey_id}")
def get_preparation(run_id: str, journey_id: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    row = db.query(BusinessJourneyPreparation).filter_by(run_id=run_id, journey_id=journey_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="PREPARATION_EVIDENCE_NOT_PERSISTED")
    return {"data": _serialize(row)}


def _serialize(row: BusinessJourneyPreparation) -> dict[str, Any]:
    return {key: getattr(row, key) for key in (
        "run_id", "journey_id", "ontology_id", "ontology_release_id", "semantic_snapshot_id",
        "pipeline_run_id", "dataset_version_id", "curated_dataset_id", "curated_review_id",
        "model_config_version_id", "structured", "model_probe", "model_calls",
    )}
