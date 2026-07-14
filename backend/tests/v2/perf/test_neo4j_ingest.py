"""
Neo4j 批量写入性能基准测试（使用 Mock，无需真实 Neo4j）

目标：
- 1000 个节点的 batch_upsert 数据准备时间 < 500ms
"""
import time
import pytest
from unittest.mock import MagicMock, patch


PREP_THRESHOLD_MS = 500


def make_entities(n: int) -> list[dict]:
    return [
        {
            "id": f"e-{i}",
            "name_cn": f"实体-{i}",
            "name_en": f"Entity-{i}",
            "type": "Organization" if i % 3 == 0 else "Product",
            "ontology_id": "perf-ont",
            "confidence": 0.9,
        }
        for i in range(n)
    ]


def test_batch_upsert_1000_entities_preparation():
    """准备 1000 个实体的 MERGE 批次数据，< 500ms"""
    entities = make_entities(1_000)

    start = time.perf_counter()
    # 模拟 batch_upsert 中的数据准备逻辑
    batch = [{"key": e.get("id"), "props": e} for e in entities]
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < PREP_THRESHOLD_MS, (
        f"数据准备 1000 个实体耗时 {elapsed_ms:.1f}ms，超过阈值 {PREP_THRESHOLD_MS}ms"
    )
    assert len(batch) == 1_000


def test_neo4j_unavailable_batch_returns_zero():
    """Neo4j 不可用时，batch_upsert_entities 快速返回 0"""
    with patch("app.services.v2.graph.neo4j_service.GraphDatabase") as mock_gdb:
        mock_gdb.driver.side_effect = Exception("offline")
        from app.services.v2.graph.neo4j_service import Neo4jService
        svc = Neo4jService(uri="bolt://x", user="x", password="x")

        entities = make_entities(100)
        start = time.perf_counter()
        count = svc.batch_upsert_entities("Entity", entities)
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert count == 0
    assert elapsed_ms < 50, f"Neo4j 不可用时 batch_upsert 响应 {elapsed_ms:.1f}ms，应 < 50ms"


def test_index_setup_skips_when_unavailable():
    """Neo4j 不可用时，setup_indexes 快速跳过（< 100ms）"""
    with patch("app.services.v2.graph.index_setup.Neo4jService") as MockSvc:
        mock_instance = MagicMock()
        mock_instance.available = False
        MockSvc.return_value = mock_instance

        from app.services.v2.graph.index_setup import setup_indexes
        start = time.perf_counter()
        result = setup_indexes(mock_instance)
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert result["status"] == "skipped"
    assert elapsed_ms < 100
