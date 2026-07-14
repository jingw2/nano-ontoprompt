from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.entity import Entity
from app.models.relation import Relation
from app.models.ontology import OntologyProject

router = APIRouter()

@router.get("")
def get_graph(ontology_id: str, limit: int = 300, db: Session = Depends(get_db), _=Depends(get_current_user)):
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(404, "Ontology not found")

    # 优先从 Neo4j 读取；无数据或不可用时回退 SQLite
    neo4j_nodes, neo4j_edges = _try_neo4j(ontology_id, limit)
    if neo4j_nodes:
        return {
            "data": {
                "nodes": neo4j_nodes,
                "edges": neo4j_edges,
                "meta": {
                    "ontology_id": ontology_id,
                    "name": project.name,
                    "entity_count": len(neo4j_nodes),
                    "relation_count": len(neo4j_edges),
                    "source": "neo4j",
                }
            }
        }

    # SQLite fallback
    entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).limit(limit).all()
    relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
    entity_ids = {e.id for e in entities}

    nodes = [
        {
            "data": {
                "id": e.id,
                "label": e.name_cn or e.name_en or e.id,
                "name_en": e.name_en,
                "name_abbr": e.name_abbr,
                "type": e.type,
                "confidence": e.confidence,
            }
        }
        for e in entities
    ]

    edges = [
        {
            "data": {
                "id": r.id,
                "source": r.source_entity,
                "target": r.target_entity,
                "label": r.type,
                "confidence": r.confidence,
            }
        }
        for r in relations
        if r.source_entity in entity_ids and r.target_entity in entity_ids
    ]

    return {
        "data": {
            "nodes": nodes,
            "edges": edges,
            "meta": {
                "ontology_id": ontology_id,
                "name": project.name,
                "entity_count": len(nodes),
                "relation_count": len(edges),
                "source": "sqlite",
            }
        }
    }


def _try_neo4j(ontology_id: str, limit: int) -> tuple[list, list]:
    """尝试从 Neo4j 读取并转换为 v1 格式；失败或无数据返回空列表。"""
    try:
        from app.services.v2.graph.neo4j_service import Neo4jService
        svc = Neo4jService()
        if not svc.available:
            return [], []
        data = svc.get_graph_data(ontology_id, limit=limit)
        svc.close()
        raw_nodes = data.get("nodes", [])
        raw_edges = data.get("edges", [])
        if not raw_nodes:
            return [], []
        # 转换为 v1 GraphTab 期望的格式
        node_ids = {n["id"] for n in raw_nodes}
        nodes = [
            {
                "data": {
                    "id": n["id"],
                    "label": (n.get("properties") or {}).get("name_cn")
                             or (n.get("properties") or {}).get("name")
                             or (n.get("labels") or [n["id"]])[0],
                    "name_en": (n.get("properties") or {}).get("name_en", ""),
                    "type": (n.get("labels") or [""])[0],
                    "confidence": (n.get("properties") or {}).get("confidence", 1.0),
                }
            }
            for n in raw_nodes
        ]
        edges = [
            {
                "data": {
                    "id": e["id"],
                    "source": e["source"],
                    "target": e["target"],
                    "label": e.get("type", "RELATED"),
                    "confidence": (e.get("properties") or {}).get("confidence", 1.0),
                }
            }
            for e in raw_edges
            if e["source"] in node_ids and e["target"] in node_ids
        ]
        return nodes, edges
    except Exception:
        return [], []

@router.post("/relations")
def create_relation(
    ontology_id: str,
    body: dict,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    from app.models.relation import Relation
    import uuid
    relation = Relation(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        source_entity=body["source_entity"],
        target_entity=body["target_entity"],
        type=body.get("type", "关联"),
        properties=body.get("properties", {}),
        confidence=body.get("confidence", 1.0),
    )
    db.add(relation); db.commit(); db.refresh(relation)
    return {"data": {"id": relation.id, "source": relation.source_entity, "target": relation.target_entity, "type": relation.type}}

@router.delete("/relations/{relation_id}", status_code=204)
def delete_relation(ontology_id: str, relation_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    r = db.query(Relation).filter(Relation.id == relation_id, Relation.ontology_id == ontology_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    db.delete(r); db.commit()
