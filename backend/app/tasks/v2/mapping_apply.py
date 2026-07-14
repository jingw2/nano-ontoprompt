"""Mapping Apply 异步 Celery 任务"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


def mapping_apply_task(mapping_id: str, ontology_id: str):
    """
    异步执行 Mapping 并写入 Neo4j。
    完整实现在 M3.4 增量更新中集成到触发链路。
    """
    from app.database import SessionLocal
    from app.services.v2.mapping.mapping_service import MappingService
    from app.models.v2.mapping import OntologyMapping
    from app.models.v2.curated import CuratedDataset

    db = SessionLocal()
    try:
        mapping = db.query(OntologyMapping).filter(OntologyMapping.id == mapping_id).first()
        if not mapping:
            logger.error(f"Mapping {mapping_id} not found")
            return

        # 从关联的 Curated Dataset 获取数据（当前使用 schema 中的 sample_rows）
        ds = db.query(CuratedDataset).filter(
            CuratedDataset.id == mapping.curated_dataset_id
        ).first()
        data = []
        if ds and ds.schema_json:
            data = ds.schema_json.get("sample_rows", [])

        svc = MappingService(db)
        result = svc.apply_mapping(mapping_id, data)
        logger.info(f"Mapping applied: {result}")
    except Exception as e:
        logger.error(f"Mapping task failed: {e}")
    finally:
        db.close()


# Celery 注册（可选）
try:
    from app.tasks.extraction import celery_app
    mapping_apply_task = celery_app.task(mapping_apply_task)
except Exception:
    pass
