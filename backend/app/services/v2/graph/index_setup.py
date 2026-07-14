"""Neo4j 索引初始化 — 提升查询性能"""
from __future__ import annotations
import logging
from app.services.v2.graph.neo4j_service import Neo4jService

logger = logging.getLogger(__name__)

# 核心索引定义
INDEXES = [
    # 按 ontology_id 过滤（所有节点都有此属性）
    "CREATE INDEX entity_ontology_id IF NOT EXISTS FOR (n:Entity) ON (n.ontology_id)",
    # 按实体 id 查找（MERGE 主键）
    "CREATE INDEX entity_id IF NOT EXISTS FOR (n:Entity) ON (n.id)",
    # 按名称搜索（关键词查找）
    "CREATE INDEX entity_name_cn IF NOT EXISTS FOR (n:Entity) ON (n.name_cn)",
    # 通用节点属性索引
    "CREATE INDEX node_ontology_id IF NOT EXISTS FOR (n) ON (n.ontology_id)",
]

# 约束定义（唯一性）
CONSTRAINTS = [
    # 同一本体内 source_row_key 唯一（防止重复 MERGE）
    # "CREATE CONSTRAINT entity_unique IF NOT EXISTS FOR (n:Entity) REQUIRE (n.ontology_id, n.id) IS UNIQUE",
    # 注：Neo4j Community 版不支持复合约束，暂时注释
]


def setup_indexes(neo4j: Neo4jService | None = None) -> dict:
    """
    执行所有索引创建语句。
    幂等操作（IF NOT EXISTS），重复执行无副作用。
    """
    svc = neo4j or Neo4jService()
    if not svc.available:
        logger.warning("Neo4j 不可用，跳过索引初始化")
        return {"status": "skipped", "reason": "neo4j_unavailable"}

    results = []
    for stmt in INDEXES:
        try:
            svc.run_cypher(stmt)
            results.append({"index": stmt[:60] + "...", "status": "ok"})
            logger.info(f"索引创建成功: {stmt[:60]}...")
        except Exception as e:
            results.append({"index": stmt[:60] + "...", "status": "error", "error": str(e)})
            logger.warning(f"索引创建失败（可能已存在）: {e}")

    svc.close()
    return {"status": "done", "results": results, "count": len(results)}
