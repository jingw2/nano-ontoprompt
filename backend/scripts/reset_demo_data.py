"""重置演示数据: 只保留一条完整测试数据链, 清空模型配置。

保留: 指定 ontology 及其 实体/关系/逻辑/动作/映射, 关联的 curated 数据集、
生成它们的 pipeline 及上游 raw 数据集。其余 ontology/pipeline/dataset 全删。
模型配置 (model_configs, 含 API Key) 全部清空。

用法: python scripts/reset_demo_data.py --keep <ontology_id前缀> [--execute]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import user, ontology, entity, relation, logic, action, file, prompt, model_config, extraction_task, rules_config  # noqa: F401
from app.models.v2 import connection, curated, mapping as v2_mapping, logic as v2_logic, action as v2_action  # noqa: F401
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.relation import Relation
from app.models.logic import LogicRule
from app.models.action import Action
from app.models.file import UploadedFile
from app.models.extraction_task import ExtractionTask
from app.models.model_config import ModelConfig
from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineVersion
from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit
from app.models.v2.mapping import OntologyMapping, OntologyLinkMapping
from app.models.v2.logic import OntologyLogicRule, OntologyStateMachine
from app.models.v2.action import OntologyActionType, OntologyActionRun
from app.models.v2.connection import Connection


def main(keep_prefix: str, execute: bool):
    db = SessionLocal()
    try:
        keep = db.query(OntologyProject).filter(OntologyProject.id.like(f"{keep_prefix}%")).first()
        if not keep:
            print(f"未找到 id 前缀为 {keep_prefix} 的 ontology")
            return
        print(f"保留 ontology: {keep.id[:8]} {keep.name}")

        # 该 ontology 引用的 curated 数据集
        keep_curated = {m.curated_dataset_id for m in db.query(OntologyMapping).filter(
            OntologyMapping.ontology_id == keep.id).all() if m.curated_dataset_id}
        # 产出这些 curated 的 pipeline 及其上游 raw 数据集
        keep_pipelines, keep_raw = set(), set()
        for pl in db.query(Pipeline).all():
            targets = set(pl.target_curated_ids or [])
            if targets & keep_curated:
                keep_pipelines.add(pl.id)
                if pl.source_dataset_id:
                    keep_raw.add(pl.source_dataset_id)
        # curated 数据集本身存放在 v2_datasets (kind=curated), 必须一并保留
        keep_dataset_ids = set(keep_raw) | set(keep_curated)
        curated_names = {c.name for c in db.query(CuratedDataset).filter(
            CuratedDataset.id.in_(keep_curated)).all()}
        for d in db.query(Dataset).filter(Dataset.kind == "curated").all():
            if d.name in curated_names:
                keep_dataset_ids.add(d.id)

        print(f"保留: curated={len(keep_curated)} pipelines={len(keep_pipelines)} datasets={len(keep_dataset_ids)}")

        # ── 删除其余 ontology 及其子数据 ────────────────────────────
        onto_ids = [o.id for o in db.query(OntologyProject).filter(OntologyProject.id != keep.id).all()]
        print(f"\n删除 {len(onto_ids)} 个其他 ontology 及其子数据")
        if execute:
            for model_, col in [
                (Relation, Relation.ontology_id), (Entity, Entity.ontology_id),
                (LogicRule, LogicRule.ontology_id), (Action, Action.ontology_id),
                (UploadedFile, UploadedFile.ontology_id), (ExtractionTask, ExtractionTask.ontology_id),
                (OntologyMapping, OntologyMapping.ontology_id),
                (OntologyLinkMapping, OntologyLinkMapping.ontology_id),
                (OntologyLogicRule, OntologyLogicRule.ontology_id),
                (OntologyStateMachine, OntologyStateMachine.ontology_id),
                (OntologyActionType, OntologyActionType.ontology_id),
                (OntologyActionRun, OntologyActionRun.ontology_id),
            ]:
                db.query(model_).filter(col.in_(onto_ids)).delete(synchronize_session=False)
            db.query(OntologyProject).filter(OntologyProject.id.in_(onto_ids)).delete(synchronize_session=False)
            db.commit()

        # ── 删除无关 pipeline ───────────────────────────────────────
        drop_pl = [p.id for p in db.query(Pipeline).all() if p.id not in keep_pipelines]
        print(f"删除 {len(drop_pl)} 个无关 pipeline")
        if execute:
            db.query(PipelineRun).filter(PipelineRun.pipeline_id.in_(drop_pl)).delete(synchronize_session=False)
            db.query(PipelineVersion).filter(PipelineVersion.pipeline_id.in_(drop_pl)).delete(synchronize_session=False)
            db.query(Pipeline).filter(Pipeline.id.in_(drop_pl)).delete(synchronize_session=False)
            db.commit()

        # ── 删除无关 dataset / curated ─────────────────────────────
        drop_ds = [d.id for d in db.query(Dataset).all() if d.id not in keep_dataset_ids]
        drop_cu = [c.id for c in db.query(CuratedDataset).all() if c.id not in keep_curated]
        print(f"删除 {len(drop_ds)} 个无关 dataset, {len(drop_cu)} 个无关 curated 记录")
        if execute:
            vids = [v.id for v in db.query(DatasetVersion).filter(DatasetVersion.dataset_id.in_(drop_ds)).all()]
            db.query(MediaItem).filter(MediaItem.dataset_version_id.in_(vids)).delete(synchronize_session=False)
            db.query(DatasetVersion).filter(DatasetVersion.dataset_id.in_(drop_ds)).delete(synchronize_session=False)
            db.query(Dataset).filter(Dataset.id.in_(drop_ds)).delete(synchronize_session=False)
            rev_ids = [r.id for r in db.query(CuratedReview).filter(CuratedReview.curated_dataset_id.in_(drop_cu)).all()]
            db.query(CuratedRowEdit).filter(CuratedRowEdit.review_id.in_(rev_ids)).delete(synchronize_session=False)
            db.query(CuratedReview).filter(CuratedReview.curated_dataset_id.in_(drop_cu)).delete(synchronize_session=False)
            db.query(CuratedDataset).filter(CuratedDataset.id.in_(drop_cu)).delete(synchronize_session=False)
            db.commit()

        # ── 连接器与模型配置 ────────────────────────────────────────
        n_conn = db.query(Connection).count()
        n_model = db.query(ModelConfig).count()
        print(f"删除 {n_conn} 个连接器, 清空 {n_model} 条模型配置 (含 API Key)")
        if execute:
            db.query(Connection).delete(synchronize_session=False)
            db.query(ModelConfig).delete(synchronize_session=False)
            db.commit()
            print("\n✅ 已执行")
        else:
            print("\n(dry-run, 加 --execute 执行)")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", required=True, help="要保留的 ontology id 前缀")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    main(args.keep, args.execute)
