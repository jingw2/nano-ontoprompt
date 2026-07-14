"""将 v1 LLM 提取结果同时写入 Neo4j + ChromaDB 的桥接器"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


class LegacyExtractionBridge:
    def __init__(self):
        self._neo4j = None
        self._chroma = None
        self._init_neo4j()
        self._init_chroma()

    def _init_chroma(self):
        try:
            from app.services.v2.vector.chroma_service import ChromaService
            self._chroma = ChromaService()
        except Exception as e:
            logger.warning(f"ChromaDB init failed in bridge: {e}")

    def _init_neo4j(self):
        try:
            from app.services.v2.graph.neo4j_service import Neo4jService
            self._neo4j = Neo4jService()
        except Exception as e:
            logger.warning(f"Neo4j init failed in bridge: {e}")

    def sync_to_neo4j(self, ontology_id: str, entities: list[dict], relations: list[dict]) -> None:
        if not self._neo4j or not self._neo4j.available:
            logger.info(f"[bridge] Neo4j unavailable — skip sync for ontology {ontology_id}")
            return

        # 实体 MERGE
        for entity in entities:
            label = entity.get("type", "Entity")
            props = {
                "id": entity.get("id", ""),
                "name_cn": entity.get("name_cn", ""),
                "name_en": entity.get("name_en", ""),
                "description": entity.get("description", ""),
                "confidence": entity.get("confidence", 0.0),
                "ontology_id": ontology_id,
            }
            try:
                self._neo4j.upsert_entity(label, props, key_field="id")
            except Exception as e:
                logger.warning(f"Neo4j entity upsert failed: {e}")

        # 关系 MERGE
        for rel in relations:
            try:
                self._neo4j.upsert_relation(
                    src_label=rel.get("source_type", "Entity"),
                    src_key=rel.get("source", ""),
                    tgt_label=rel.get("target_type", "Entity"),
                    tgt_key=rel.get("target", ""),
                    rel_type=rel.get("type", "RELATES_TO"),
                    props={"confidence": rel.get("confidence", 0.0), "ontology_id": ontology_id},
                )
            except Exception as e:
                logger.warning(f"Neo4j relation upsert failed: {e}")

        logger.info(f"[bridge] Neo4j sync: ontology={ontology_id}, {len(entities)} entities, {len(relations)} relations")

    def sync_to_chroma(self, ontology_id: str, entities: list[dict]) -> None:
        """向 ChromaDB 存储嵌入"""
        if not self._chroma or not self._chroma.available:
            logger.info(f"[bridge] ChromaDB unavailable — skip sync for ontology {ontology_id}")
            return
        count = self._chroma.upsert_entities(ontology_id, entities)
        logger.info(f"[bridge] ChromaDB sync: ontology={ontology_id}, {count} entities upserted")

    def sync_all(self, ontology_id: str, entities: list[dict], relations: list[dict]) -> None:
        self.sync_to_neo4j(ontology_id, entities, relations)
        self.sync_to_chroma(ontology_id, entities)
