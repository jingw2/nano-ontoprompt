"""v2 Graph API — Neo4j 기반"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.deps import get_current_user
from app.database import SessionLocal

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_neo4j():
    from app.services.v2.graph.neo4j_service import Neo4jService
    return Neo4jService()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CypherRequest(BaseModel):
    query: str
    params: dict = {}


@router.get("/{ontology_id}/graph")
def get_graph(ontology_id: str, limit: int = 200, label_filter: str | None = None):
    """온톨로지 그래프 데이터 반환 (Neovis.js 호환 포맷)"""
    svc = get_neo4j()
    if not svc.available:
        return {"nodes": [], "edges": [], "neo4j_available": False}
    data = svc.get_graph_data(ontology_id, limit=limit, label_filter=label_filter)
    data["neo4j_available"] = True
    svc.close()
    return data


@router.get("/{ontology_id}/graph/quality")
def graph_quality(ontology_id: str):
    from app.models.entity import Entity
    from app.models.relation import Relation
    from collections import Counter

    db = SessionLocal()
    try:
        entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
        relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
        entity_ids = {e.id for e in entities}
        connected_ids = {r.source_entity for r in relations} | {r.target_entity for r in relations}
        orphan_relations = [
            r.id for r in relations
            if r.source_entity not in entity_ids or r.target_entity not in entity_ids
        ]
        isolated = [e.id for e in entities if e.id not in connected_ids]
        names = [e.name_cn for e in entities if e.name_cn]
        duplicate_names = {name: count for name, count in Counter(names).items() if count > 1}
        object_types = Counter(e.type or "Entity" for e in entities)
        relation_types = Counter(r.type or "RELATED" for r in relations)
        node_count = len(entities)
        edge_count = len(relations)
        duplicate_name_instances = sum(duplicate_names.values())
        quality_score = 1.0
        if node_count:
            quality_score -= min(0.4, len(isolated) / node_count * 0.4)
            quality_score -= min(0.25, duplicate_name_instances / node_count * 0.25)
        if edge_count:
            quality_score -= min(0.25, len(orphan_relations) / edge_count * 0.25)
        return {
            "ontology_id": ontology_id,
            "node_count": node_count,
            "edge_count": edge_count,
            "isolated_node_count": len(isolated),
            "orphan_relation_count": len(orphan_relations),
            "duplicate_display_name_count": duplicate_name_instances,
            "object_type_counts": dict(object_types),
            "relation_type_counts": dict(relation_types),
            "relation_density": round(edge_count / node_count, 4) if node_count else 0,
            "quality_score": round(max(0.0, quality_score), 4),
            "samples": {
                "isolated_node_ids": isolated[:10],
                "orphan_relation_ids": orphan_relations[:10],
                "duplicate_display_names": dict(list(duplicate_names.items())[:10]),
            },
        }
    finally:
        db.close()


@router.get("/{ontology_id}/integrations/status")
def integration_status(ontology_id: str):
    neo = get_neo4j()
    neo_available = neo.available
    if neo_available:
        neo.close()
    from app.services.v2.vector.chroma_service import ChromaService
    chroma = ChromaService()
    return {
        "ontology_id": ontology_id,
        "neo4j": {"available": neo_available},
        "chroma": {"available": chroma.available, "entity_count": chroma.count(ontology_id)},
    }


@router.post("/{ontology_id}/graph/cypher")
def run_cypher(ontology_id: str, body: CypherRequest):
    """Cypher 쿼리 실행 (검증 후)"""
    # 기본 안전성 검사 — WRITE 쿼리 차단
    query_upper = body.query.upper().strip()
    write_keywords = ("CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP")
    for kw in write_keywords:
        if kw in query_upper:
            raise HTTPException(400, f"Write queries not allowed via this endpoint: {kw}")

    svc = get_neo4j()
    if not svc.available:
        return {"results": [], "neo4j_available": False}
    results = svc.run_cypher(body.query, body.params)
    svc.close()
    return {"results": results, "neo4j_available": True}


@router.get("/{ontology_id}/graph/neighbors/{node_id}")
def get_neighbors(ontology_id: str, node_id: str, depth: int = 1):
    """노드 이웃 조회"""
    svc = get_neo4j()
    if not svc.available:
        return {"nodes": [], "edges": [], "neo4j_available": False}
    query = f"""
    MATCH (n)-[r*1..{min(depth, 5)}]-(m)
    WHERE elementId(n) = $node_id AND n.ontology_id = $ontology_id
    RETURN n, r, m LIMIT 100
    """
    results = svc.run_cypher(query, {"node_id": node_id, "ontology_id": ontology_id})
    svc.close()
    return {"results": results, "neo4j_available": True}


# ── 自然语言查询 ──────────────────────────────────────────────────────

class NLQueryRequest(BaseModel):
    question: str
    schema: dict = {}


@router.post("/{ontology_id}/graph/ask")
def nl_query(ontology_id: str, body: NLQueryRequest):
    """自然语言 → Cypher → 图数据"""
    from app.services.v2.graph.nl2cypher import NL2CypherService
    nl_svc = NL2CypherService()
    plan = nl_svc.translate(body.question, body.schema)

    svc = get_neo4j()
    if not svc.available:
        return {"results": [], "cypher": plan.cypher, "explanation": plan.explanation, "neo4j_available": False}

    try:
        results = svc.run_cypher(plan.cypher, {"ontology_id": ontology_id})
        svc.close()
        return {
            "results": results,
            "cypher": plan.cypher,
            "explanation": plan.explanation,
            "confidence": plan.confidence,
            "neo4j_available": True,
        }
    except Exception as e:
        svc.close()
        return {"results": [], "cypher": plan.cypher, "error": str(e), "neo4j_available": True}


# ── 高级图分析 ─────────────────────────────────────────────────────────

@router.get("/{ontology_id}/graph/path")
def graph_path(ontology_id: str, src: str, tgt: str):
    """两节点间最短路径"""
    from app.services.v2.graph.graph_analytics import GraphAnalyticsService
    svc = GraphAnalyticsService()
    return svc.shortest_path(ontology_id, src, tgt)


@router.get("/{ontology_id}/graph/degree/{node_id}")
def node_degree(ontology_id: str, node_id: str):
    """查询节点度数（入度 + 出度）"""
    from app.services.v2.graph.graph_analytics import GraphAnalyticsService
    svc = GraphAnalyticsService()
    return svc.node_degree(ontology_id, node_id)


@router.get("/{ontology_id}/graph/top-nodes")
def top_nodes(ontology_id: str, limit: int = 10):
    """返回连接数最多的 Top-N 节点"""
    from app.services.v2.graph.graph_analytics import GraphAnalyticsService
    svc = GraphAnalyticsService()
    return {"nodes": svc.top_connected_nodes(ontology_id, limit)}


@router.post("/{ontology_id}/graph/sync")
def sync_graph(ontology_id: str):
    """将 SQLite 实体/关系全量同步到 Neo4j"""
    from app.database import SessionLocal
    from app.models.entity import Entity
    from app.models.relation import Relation

    neo = get_neo4j()
    if not neo.available:
        return {"synced": False, "reason": "Neo4j unavailable"}

    db = SessionLocal()
    try:
        entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
        relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()

        # Build entity id -> neo4j label map (use type as label, fallback Entity)
        entity_label_map: dict[str, str] = {}

        # Batch upsert entities
        batch = []
        for e in entities:
            label = (e.type or "Entity").replace(" ", "_")
            entity_label_map[e.id] = label
            props = {
                **(e.properties or {}),
                "id": e.id,           # SQLite UUID 优先，覆盖 properties 里的 id
                "source_id": e.id,
                "ontology_id": ontology_id,
                "name_cn": e.name_cn or "",
                "name": e.name_cn or "",
                "name_en": e.name_en or "",
                "type": e.type or "",
                "description": e.description or "",
                "confidence": e.confidence or 1.0,
                "version": e.version or "v0.1",
            }
            # Use generic label for batch
            batch.append(props)

        # Upsert all as generic "OntologyEntity" first (fast batch)
        synced_entities = neo.batch_upsert_entities("OntologyEntity", batch, key_field="id")

        # Upsert relations
        synced_relations = 0
        for r in relations:
            src_label = entity_label_map.get(r.source_entity, "OntologyEntity")
            tgt_label = entity_label_map.get(r.target_entity, "OntologyEntity")
            rel_type = (r.type or "RELATED").upper().replace(" ", "_").replace("-", "_")
            ok = neo.upsert_relation(
                "OntologyEntity", r.source_entity,
                "OntologyEntity", r.target_entity,
                rel_type,
                props={"ontology_id": ontology_id, "confidence": r.confidence or 1.0},
            )
            if ok:
                synced_relations += 1

        neo.close()
        return {
            "synced": True,
            "entities": synced_entities,
            "relations": synced_relations,
            "ontology_id": ontology_id,
        }
    finally:
        db.close()
