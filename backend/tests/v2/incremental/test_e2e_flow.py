"""增量更新编排器端到端流程测试（全 Mock）"""
import pytest
from unittest.mock import MagicMock, patch, call
from app.services.v2.incremental.orchestrator import IncrementalOrchestrator
from app.models.v2.pipeline import Pipeline, PipelineRun
from app.models.v2.curated import CuratedDataset, CuratedReview
from app.models.v2.mapping import OntologyMapping
from datetime import datetime, timezone


# ── 测试辅助 ──────────────────────────────────────────────────────────

def make_pipeline(pipeline_id="pl-1", dataset_id="ds-1", auto_trigger=True, target_ids=None):
    pl = Pipeline(
        id=pipeline_id,
        name="test_pipeline",
        source_dataset_id=dataset_id,
        route="A",
        spec={"trigger": {"on_dataset_version": auto_trigger}},
        target_curated_ids=target_ids or ["curated-1"],
        status="active",
    )
    return pl


def make_curated_ds(ds_id="curated-1", status="approved"):
    return CuratedDataset(id=ds_id, name="test_curated", status=status, pipeline_id="pl-1")


def make_review(review_id="rev-1", ds_id="curated-1", status="approved"):
    return CuratedReview(id=review_id, curated_dataset_id=ds_id, status=status)


def make_mapping(mapping_id="map-1", ds_id="curated-1", auto_apply=True):
    return OntologyMapping(
        id=mapping_id,
        ontology_id="ont-1",
        curated_dataset_id=ds_id,
        entity_class="Order",
        field_mapping={"order_id": "id", "__auto_apply_on_review__": auto_apply},
        status="applied",
    )


def make_run(run_id="run-1", pipeline_id="pl-1", status="success"):
    return PipelineRun(id=run_id, pipeline_id=pipeline_id, status=status)


# ── on_connection_sync 测试 ───────────────────────────────────────────

def test_on_connection_sync_triggers_pipeline():
    db = MagicMock()
    pipeline = make_pipeline(auto_trigger=True)

    db.query.return_value.filter.return_value.filter.return_value.all.return_value = [pipeline]
    db.add = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock(side_effect=lambda obj: setattr(obj, 'id', 'new-run-1'))

    orch = IncrementalOrchestrator(db)
    with patch("app.services.v2.incremental.orchestrator.IncrementalOrchestrator._trigger_pipeline",
               return_value="new-run-1") as mock_trigger:
        result = orch.on_connection_sync("conn-1", "ds-1")

    assert len(result["triggered_pipelines"]) == 1
    mock_trigger.assert_called_once_with("pl-1", mode="incremental")


def test_on_connection_sync_no_auto_trigger():
    db = MagicMock()
    pipeline = make_pipeline(auto_trigger=False)
    db.query.return_value.filter.return_value.filter.return_value.all.return_value = [pipeline]

    orch = IncrementalOrchestrator(db)
    result = orch.on_connection_sync("conn-1", "ds-1")
    assert result["triggered_pipelines"] == []


def test_on_connection_sync_no_pipelines():
    db = MagicMock()
    db.query.return_value.filter.return_value.filter.return_value.all.return_value = []

    orch = IncrementalOrchestrator(db)
    result = orch.on_connection_sync("conn-1", "ds-1")
    assert result["triggered_pipelines"] == []


# ── on_pipeline_success 测试 ─────────────────────────────────────────

def test_on_pipeline_success_resets_curated_status():
    db = MagicMock()
    run = make_run(status="success")
    pipeline = make_pipeline(target_ids=["curated-1"])
    curated = make_curated_ds(status="approved")

    def query_side(model):
        q = MagicMock()
        if model == PipelineRun:
            q.filter.return_value.first.return_value = run
        elif model == Pipeline:
            q.filter.return_value.first.return_value = pipeline
        elif model == CuratedDataset:
            q.filter.return_value.first.return_value = curated
        return q

    db.query.side_effect = query_side
    db.commit = MagicMock()

    orch = IncrementalOrchestrator(db)
    result = orch.on_pipeline_success("run-1")

    assert "curated-1" in result["updated_datasets"]
    assert curated.status == "pending_review"


def test_on_pipeline_success_skips_if_run_not_found():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    orch = IncrementalOrchestrator(db)
    result = orch.on_pipeline_success("bad-run")
    assert result["status"] == "skipped"


# ── on_review_approved 测试 ───────────────────────────────────────────

def test_on_review_approved_triggers_mapping():
    db = MagicMock()
    review = make_review(status="approved")
    mapping = make_mapping(auto_apply=True)

    def query_side(model):
        q = MagicMock()
        if model == CuratedReview:
            q.filter.return_value.first.return_value = review
        elif model == OntologyMapping:
            q.filter.return_value.filter.return_value.all.return_value = [mapping]
        return q

    db.query.side_effect = query_side

    orch = IncrementalOrchestrator(db)
    with patch.object(orch, '_trigger_mapping_apply', return_value="task-1") as mock_trigger:
        result = orch.on_review_approved("rev-1")

    assert len(result["triggered_mappings"]) == 1
    mock_trigger.assert_called_once_with("map-1", "ont-1")


def test_on_review_approved_no_auto_apply():
    db = MagicMock()
    review = make_review(status="approved")
    mapping = make_mapping(auto_apply=False)

    def query_side(model):
        q = MagicMock()
        if model == CuratedReview:
            q.filter.return_value.first.return_value = review
        elif model == OntologyMapping:
            q.filter.return_value.filter.return_value.all.return_value = [mapping]
        return q

    db.query.side_effect = query_side

    orch = IncrementalOrchestrator(db)
    result = orch.on_review_approved("rev-1")
    assert result["triggered_mappings"] == []


def test_on_review_approved_skips_non_approved():
    db = MagicMock()
    review = make_review(status="pending")
    db.query.return_value.filter.return_value.first.return_value = review

    orch = IncrementalOrchestrator(db)
    result = orch.on_review_approved("rev-1")
    assert result["status"] == "skipped"


# ── E2E 完整链路 ──────────────────────────────────────────────────────

def test_full_incremental_chain():
    """
    模拟完整增量链路：
    Connection 同步 → Pipeline 触发 → 审核通过 → Mapping 自动应用
    """
    db = MagicMock()

    pipeline = make_pipeline(auto_trigger=True, target_ids=["curated-1"])
    run = make_run(status="success")
    curated = make_curated_ds(status="approved")
    review = make_review(status="approved")
    mapping = make_mapping(auto_apply=True)

    call_count = {"query": 0}

    def query_side(model):
        q = MagicMock()
        if model == Pipeline:
            q.filter.return_value.filter.return_value.all.return_value = [pipeline]
            q.filter.return_value.first.return_value = pipeline
        elif model == PipelineRun:
            q.filter.return_value.first.return_value = run
        elif model == CuratedDataset:
            q.filter.return_value.first.return_value = curated
        elif model == CuratedReview:
            q.filter.return_value.first.return_value = review
        elif model == OntologyMapping:
            q.filter.return_value.filter.return_value.all.return_value = [mapping]
        return q

    db.query.side_effect = query_side
    db.add = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock(side_effect=lambda obj: None)

    orch = IncrementalOrchestrator(db)

    # Step 1: Connection 同步完成
    with patch.object(orch, '_trigger_pipeline', return_value="run-new"):
        sync_result = orch.on_connection_sync("conn-1", "ds-1")
    assert len(sync_result["triggered_pipelines"]) == 1

    # Step 2: Pipeline 成功 → Curated 变为 pending_review
    pipeline_result = orch.on_pipeline_success("run-1")
    assert curated.status == "pending_review"

    # Step 3: 审核通过 → Mapping 自动触发
    review.status = "approved"  # 重新设为 approved
    with patch.object(orch, '_trigger_mapping_apply', return_value="task-1"):
        approve_result = orch.on_review_approved("rev-1")
    assert len(approve_result["triggered_mappings"]) == 1
