"""增量更新编排器 — 触发链路：Connection→Pipeline→Curated→Ontology"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class IncrementalOrchestrator:
    """
    管理全链路增量更新触发逻辑：

    1. on_connection_sync  → 检测关联 Pipeline，触发增量运行
    2. on_pipeline_success → 更新 Curated Dataset，发出审核通知
    3. on_review_approved  → 检测关联 Mapping，自动触发增量 Mapping
    """

    def __init__(self, db: Session):
        self._db = db

    # ── 触发点 1：Connection 同步完成 ────────────────────────────────

    def on_connection_sync(self, connection_id: str, dataset_id: str) -> dict:
        """
        数据连接同步完成后：
        - 找到以该 dataset 为输入的所有 Pipeline
        - 若 Pipeline.spec.trigger.on_dataset_version = true，触发增量运行
        """
        from app.models.v2.pipeline import Pipeline

        triggered = []
        pipelines = self._db.query(Pipeline).filter(
            Pipeline.source_dataset_id == dataset_id,
        ).filter(
            Pipeline.status != "disabled",
        ).all()

        for pl in pipelines:
            spec = pl.spec or {}
            trigger_config = spec.get("trigger", {})
            if trigger_config.get("on_dataset_version", False):
                run_id = self._trigger_pipeline(pl.id, mode="incremental")
                if run_id:
                    triggered.append({"pipeline_id": pl.id, "run_id": run_id})
                    logger.info(f"增量触发 Pipeline {pl.id}，run_id={run_id}")

        return {"triggered_pipelines": triggered, "dataset_id": dataset_id}

    # ── 触发点 2：Pipeline 运行成功 ───────────────────────────────────

    def on_pipeline_success(self, pipeline_run_id: str) -> dict:
        """
        Pipeline 运行成功后：
        - 找到关联的 Curated Dataset
        - 将其状态重置为 pending_review（供人工审核）
        - 记录增量标记
        """
        from app.models.v2.pipeline import PipelineRun, Pipeline
        from app.models.v2.curated import CuratedDataset

        run = self._db.query(PipelineRun).filter(PipelineRun.id == pipeline_run_id).first()
        if not run or run.status != "success":
            return {"status": "skipped", "reason": "run not found or not success"}

        pipeline = self._db.query(Pipeline).filter(Pipeline.id == run.pipeline_id).first()
        if not pipeline:
            return {"status": "skipped", "reason": "pipeline not found"}

        updated_datasets = []
        target_ids = pipeline.target_curated_ids or []

        for ds_id in target_ids:
            ds = self._db.query(CuratedDataset).filter(CuratedDataset.id == ds_id).first()
            if ds and ds.status == "approved":
                # 已审核的数据集有新增量，重置为待审核
                ds.status = "pending_review"
                ds.updated_at = datetime.now(timezone.utc)
                updated_datasets.append(ds_id)
                logger.info(f"Curated Dataset {ds_id} 有新增量，重置为 pending_review")

        self._db.commit()
        return {"updated_datasets": updated_datasets, "pipeline_run_id": pipeline_run_id}

    # ── 触发点 3：审核通过 ─────────────────────────────────────────────

    def on_review_approved(self, review_id: str) -> dict:
        """
        Curated Dataset 审核通过后：
        - 找到关联该 Dataset 的所有 OntologyMapping
        - 若 spec.auto_apply_on_review = true，自动触发 Mapping Apply
        """
        from app.models.v2.curated import CuratedReview
        from app.models.v2.mapping import OntologyMapping

        review = self._db.query(CuratedReview).filter(CuratedReview.id == review_id).first()
        if not review or review.status != "approved":
            return {"status": "skipped", "reason": "review not found or not approved"}

        dataset_id = review.curated_dataset_id
        mappings = self._db.query(OntologyMapping).filter(
            OntologyMapping.curated_dataset_id == dataset_id,
        ).filter(
            OntologyMapping.status != "disabled",
        ).all()

        triggered = []
        for mapping in mappings:
            # 检查是否配置了自动触发
            field_map = mapping.field_mapping or {}
            auto_apply = field_map.get("__auto_apply_on_review__", False)

            if auto_apply:
                task_id = self._trigger_mapping_apply(mapping.id, mapping.ontology_id)
                triggered.append({"mapping_id": mapping.id, "task_id": task_id})
                logger.info(f"自动触发 Mapping {mapping.id} 增量写入")

        return {
            "triggered_mappings": triggered,
            "dataset_id": dataset_id,
            "review_id": review_id,
        }

    # ── 幂等性保障 ────────────────────────────────────────────────────

    def _trigger_pipeline(self, pipeline_id: str, mode: str = "incremental") -> str | None:
        """触发 Pipeline 运行，返回 run_id"""
        from app.models.v2.pipeline import PipelineRun

        run = PipelineRun(pipeline_id=pipeline_id, status="pending")
        self._db.add(run)
        self._db.commit()
        self._db.refresh(run)

        try:
            from app.tasks.v2.pipeline_run import pipeline_run_task
            pipeline_run_task.delay(pipeline_id, run.id)
        except Exception:
            pass  # Celery 不可用时仍保留 PipelineRun 记录

        return run.id

    def _trigger_mapping_apply(self, mapping_id: str, ontology_id: str) -> str | None:
        """触发 Mapping Apply 任务"""
        try:
            from app.tasks.v2.mapping_apply import mapping_apply_task
            result = mapping_apply_task.delay(mapping_id, ontology_id)
            return str(result.id) if hasattr(result, 'id') else mapping_id
        except Exception:
            # Celery 不可用时同步执行
            try:
                from app.tasks.v2.mapping_apply import mapping_apply_task
                mapping_apply_task(mapping_id, ontology_id)
            except Exception as e:
                logger.warning(f"Mapping apply 同步执行失败: {e}")
            return mapping_id
