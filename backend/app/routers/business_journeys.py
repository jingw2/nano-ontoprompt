"""Narrow durable evidence API used by the real-model journey gate.

The preparation model call happens in the trusted gate process, which owns
the provider credential. This write endpoint is therefore bound to the
configured gate user, while release tool descriptors are independently
derived from the application's published catalog and active data grant.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_current_user, get_db, require_editor
from app.models.business_journey import (
    PREPARATION_SLOT_SCOPE,
    BusinessJourneyModelCall,
    BusinessJourneyPreparation,
)
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.ontology_release import OntologyRelease
from app.models.semantic_snapshot import SemanticSnapshotInput
from app.models.user import User
from app.services.runtime.snapshots import SnapshotValidationError, materialize_snapshot
from app.models.v2.curated import CuratedReview
from app.models.v2.pipeline import PipelineRun, PipelineRunInput
from app.services.agent.catalog import ontology_tool_catalog
from app.services.business_journey_ledger import JourneyLedgerError, record_preparation_call

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


def _require_gate_identity(current_user: User) -> None:
    gate_username = settings.business_journey_gate_username.strip()
    if (not gate_username or gate_username == settings.first_admin_user
            or current_user.username != gate_username):
        raise HTTPException(status_code=403, detail="BUSINESS_JOURNEY_GATE_IDENTITY_REQUIRED")


def _active_grant_capabilities(db: Session, ontology_id: str, user_id: str) -> set[str]:
    now = datetime.now(timezone.utc)
    capabilities: set[str] = set()
    grants = db.query(OntologyDataGrant).filter_by(
        ontology_id=ontology_id, user_id=user_id, status="active",
    ).all()
    for grant in grants:
        valid_from = grant.valid_from
        valid_until = grant.valid_until
        if valid_from is not None and valid_from.tzinfo is None:
            valid_from = valid_from.replace(tzinfo=timezone.utc)
        if valid_until is not None and valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        if (valid_from is not None and valid_from > now) or (valid_until is not None and now >= valid_until):
            continue
        capabilities.update(str(capability) for capability in (grant.capabilities or ()))
    return capabilities


def _derive_granted_descriptor_ids(db: Session, release: OntologyRelease, user_id: str) -> list[str]:
    """Return descriptors exposed by this exact published release and grant."""
    if release.status != "published":
        raise HTTPException(status_code=422, detail="ONTOLOGY_RELEASE_NOT_PUBLISHED")
    catalog = ontology_tool_catalog(db, release.ontology_id)
    if not catalog.get("published") or catalog.get("release_id") != release.id:
        raise HTTPException(status_code=422, detail="MCP_CATALOG_RELEASE_MISMATCH")
    grant_capabilities = _active_grant_capabilities(db, release.ontology_id, user_id)
    descriptor_ids = [
        str(tool["descriptor_id"])
        for tool in catalog.get("tools") or ()
        if tool.get("descriptor_id") and tool.get("capability") in grant_capabilities
    ]
    if not descriptor_ids:
        raise HTTPException(status_code=422, detail="MCP_DESCRIPTOR_GRANT_MISSING")
    return descriptor_ids


@router.post("/preparations", status_code=201)
def create_preparation(
    body: PreparationEvidenceIn, db: Session = Depends(get_db), current_user: User = Depends(require_editor),
):
    _require_gate_identity(current_user)
    _validate_model_evidence(body)
    if db.query(BusinessJourneyPreparation).filter_by(run_id=body.run_id, journey_id=body.journey_id).first():
        raise HTTPException(status_code=409, detail="PREPARATION_EVIDENCE_EXISTS")
    run = db.get(PipelineRun, body.pipeline_run_id)
    if run is None or not run.is_governed:
        raise HTTPException(status_code=422, detail="PIPELINE_RUN_NOT_GOVERNED")
    if body.dataset_version_id != run.dataset_version_id:
        raise HTTPException(status_code=422, detail="PIPELINE_OUTPUT_VERSION_MISMATCH")
    curated_dataset_id = str((run.stats or {}).get("curated_dataset_id") or "")
    if body.curated_dataset_id != curated_dataset_id:
        raise HTTPException(status_code=422, detail="CURATED_DATASET_RUN_MISMATCH")
    review = db.get(CuratedReview, body.curated_review_id)
    if (review is None or review.curated_dataset_id != curated_dataset_id or review.status != "approved"
            or review.pipeline_run_id != body.pipeline_run_id):
        raise HTTPException(status_code=422, detail="CURATED_APPROVAL_RUN_MISMATCH")
    # `materialize_snapshot` below is only ever given `ontology_release_id`
    # -- it has no way to notice if the caller's separately-submitted
    # `ontology_id` names an unrelated (or fabricated) ontology, since
    # nothing else in this handler ever reads that field back against the
    # database. A release always belongs to exactly one ontology
    # (`ontology_releases.ontology_id`), so that's the one independent
    # source of truth available to cross-check it against.
    release = db.get(OntologyRelease, body.ontology_release_id)
    if release is None or release.ontology_id != body.ontology_id:
        raise HTTPException(status_code=422, detail="ONTOLOGY_RELEASE_MISMATCH")
    descriptor_ids = _derive_granted_descriptor_ids(db, release, current_user.id)
    inputs = db.query(PipelineRunInput).filter_by(pipeline_run_id=body.pipeline_run_id).all()
    if body.dataset_version_id not in {item.dataset_version_id for item in inputs}:
        raise HTTPException(status_code=422, detail="PIPELINE_OUTPUT_LINEAGE_MISSING")
    try:
        snapshot = materialize_snapshot(
            db, ontology_release_id=body.ontology_release_id,
            dataset_version_ids=tuple(item.dataset_version_id for item in inputs), created_by=current_user.id,
        )
    except SnapshotValidationError as exc:
        raise HTTPException(status_code=422, detail=f"SEMANTIC_SNAPSHOT_MATERIALIZATION_FAILED:{exc.reason_code}") from exc
    row = BusinessJourneyPreparation(
        **body.model_dump(exclude={"model_calls"}), semantic_snapshot_id=snapshot.id,
        mcp_descriptor_ids=descriptor_ids,
        model_calls=[call.model_dump() for call in body.model_calls],
    )
    db.add(row)
    try:
        record_preparation_call(
            db, run_id=body.run_id, journey_id=body.journey_id,
            call=row.model_calls[0], model_config_version_id=body.model_config_version_id,
        )
    except JourneyLedgerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return {"data": _serialize(row, db=db)}


@router.get("/preparations/{run_id}/{journey_id}")
def get_preparation(
    run_id: str, journey_id: str, turn_id: str | None = Query(default=None),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    _require_gate_identity(current_user)
    row = db.query(BusinessJourneyPreparation).filter_by(run_id=run_id, journey_id=journey_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="PREPARATION_EVIDENCE_NOT_PERSISTED")
    return {"data": _serialize(row, db=db, turn_id=turn_id)}


def _serialize(
    row: BusinessJourneyPreparation, *, db: Session | None = None, turn_id: str | None = None,
) -> dict[str, Any]:
    result = {key: getattr(row, key) for key in (
        "run_id", "journey_id", "ontology_id", "ontology_release_id", "semantic_snapshot_id",
        "pipeline_run_id", "dataset_version_id", "curated_dataset_id", "curated_review_id",
        "model_config_version_id", "mcp_descriptor_ids", "structured", "model_probe", "model_calls",
    )}
    if db is not None:
        ledger_query = db.query(BusinessJourneyModelCall).filter_by(
            run_id=row.run_id, journey_id=row.journey_id, status="finalized",
        )
        if turn_id:
            # Keep preparation slot 1 and only the exact runtime slots for
            # the turn whose browser evidence is being verified.  Without
            # this scope, a retry/second turn would make the verifier read
            # more than its required three-call chain.
            ledger_query = ledger_query.filter(or_(
                BusinessJourneyModelCall.turn_id == PREPARATION_SLOT_SCOPE,
                BusinessJourneyModelCall.turn_id == turn_id,
            ))
        ledger_calls = [
            {key: getattr(item, key) for key in (
                "turn_id", "call_kind", "logical_call_index", "correlation_id", "requested_model",
                "observed_model", "http_attempts", "retry_count", "model_config_version_id",
            )}
            for item in ledger_query.order_by(
                BusinessJourneyModelCall.logical_call_index,
                BusinessJourneyModelCall.created_at,
            ).all()
        ]
        # Preparation evidence remains exactly slot 1. The separate ledger is
        # the authoritative all-phase source for verification after runtime.
        result["model_calls"] = [call for call in ledger_calls if call["logical_call_index"] == 1]
        result["model_call_ledger"] = ledger_calls
        result["snapshot_inputs"] = [
            {"snapshot_id": item.snapshot_id, "dataset_version_id": item.dataset_version_id,
             "pipeline_run_id": item.pipeline_run_id}
            for item in db.query(SemanticSnapshotInput).filter_by(snapshot_id=row.semantic_snapshot_id).all()
        ]
    return result
