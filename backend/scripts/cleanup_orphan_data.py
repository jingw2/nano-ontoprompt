"""清理测试残留数据:

1. routeC-* 测试 Pipeline(验收脚本创建的临时管道)及其 runs/versions
2. 孤儿 Dataset:所有版本的存储对象都已丢失(MinIO/本地都找不到),无法再被任何流程使用

默认 dry-run 只打印,加 --execute 才真正删除。
用法: python scripts/cleanup_orphan_data.py [--execute]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
# 注册全部表, 否则 SQLAlchemy 在 flush 时无法解析 FK
from app.models import user, ontology, entity, relation, logic, action, file, prompt, model_config, extraction_task, rules_config  # noqa: F401
from app.models.v2 import connection, curated, mapping, logic as v2_logic, action as v2_action  # noqa: F401
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineVersion
from app.services.storage_service import StorageService


def main(execute: bool):
    db = SessionLocal()
    storage = StorageService()
    try:
        # 1. routeC-* 测试 pipeline
        test_pipelines = db.query(Pipeline).filter(Pipeline.name.like("routeC-%")).all()
        print(f"routeC-* 测试 Pipeline: {len(test_pipelines)} 个")
        for pl in test_pipelines:
            runs = db.query(PipelineRun).filter(PipelineRun.pipeline_id == pl.id).count()
            print(f"  - {pl.name} (runs={runs})")
            if execute:
                db.query(PipelineRun).filter(PipelineRun.pipeline_id == pl.id).delete()
                db.query(PipelineVersion).filter(PipelineVersion.pipeline_id == pl.id).delete()
                db.delete(pl)
        if execute:
            db.commit()

        # 2. 存储对象丢失的孤儿 Dataset
        datasets = db.query(Dataset).all()
        orphans = []
        for ds in datasets:
            versions = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == ds.id).all()
            if not versions:
                orphans.append((ds, versions))
                continue
            uris = [v.storage_uri for v in versions if v.storage_uri]
            if uris and not any(storage.object_exists(uri) for uri in uris):
                orphans.append((ds, versions))

        print(f"\n孤儿 Dataset(存储对象丢失或无版本): {len(orphans)} / {len(datasets)} 个")
        for ds, versions in orphans[:15]:
            print(f"  - {ds.name} kind={ds.kind} versions={len(versions)}")
        if len(orphans) > 15:
            print(f"  ... 等共 {len(orphans)} 个")

        if execute:
            for ds, versions in orphans:
                for v in versions:
                    db.delete(v)
                db.delete(ds)
            db.commit()
            print("\n✅ 已删除")
        else:
            print("\n(dry-run,加 --execute 执行删除)")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    main(args.execute)
