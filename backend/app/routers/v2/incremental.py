"""v2 增量更新触发 API"""
from __future__ import annotations
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.deps import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/connections/{connection_id}/sync-complete")
def notify_sync_complete(
    connection_id: str,
    dataset_id: str,
    db: Session = Depends(get_db),
):
    """
    数据连接同步完成的回调通知，触发下游 Pipeline 增量运行。
    """
    from app.services.v2.incremental.orchestrator import IncrementalOrchestrator
    orch = IncrementalOrchestrator(db)
    return orch.on_connection_sync(connection_id, dataset_id)


@router.post("/pipeline-runs/{run_id}/complete")
def notify_pipeline_complete(run_id: str, db: Session = Depends(get_db)):
    """
    Pipeline 运行完成的回调通知，更新 Curated Dataset 状态。
    """
    from app.services.v2.incremental.orchestrator import IncrementalOrchestrator
    orch = IncrementalOrchestrator(db)
    return orch.on_pipeline_success(run_id)


@router.post("/reviews/{review_id}/approve-trigger")
def trigger_on_approve(review_id: str, db: Session = Depends(get_db)):
    """
    审核通过后自动触发 Ontology Mapping 增量写入。
    """
    from app.services.v2.incremental.orchestrator import IncrementalOrchestrator
    orch = IncrementalOrchestrator(db)
    return orch.on_review_approved(review_id)
