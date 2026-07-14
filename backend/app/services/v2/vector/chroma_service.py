"""ChromaDB 向量数据库服务"""
from __future__ import annotations
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

try:
    import chromadb
except ImportError:
    chromadb = None

# 连接失败后多少秒内不再重试 (heartbeat 超时会阻塞每个请求数秒)
_RETRY_INTERVAL = 60.0


class ChromaService:
    """ChromaDB 连接与向量存储/检索服务

    使用默认配置时全进程共享同一个 client; 连接失败后 60 秒内直接判定不可用,
    避免每次实例化都触发 heartbeat 超时 (此前 integrations/status 每次卡 ~6 秒)。
    """

    _shared_client = None
    _shared_unavailable_until: float = 0.0

    def __init__(self, host: str | None = None, port: int | None = None):
        from app.config import settings
        self._is_default = host is None and port is None
        self._host = host or settings.chroma_host
        self._port = port or settings.chroma_port
        self._client = None
        self._available = False
        self._init_client()

    def _init_client(self):
        cls = ChromaService
        if self._is_default:
            if cls._shared_client is not None:
                self._client = cls._shared_client
                self._available = True
                return
            if time.monotonic() < cls._shared_unavailable_until:
                return
        try:
            if chromadb is None:
                raise ImportError("chromadb not installed")
            client = chromadb.HttpClient(host=self._host, port=self._port)
            client.heartbeat()
            self._client = client
            self._available = True
            if self._is_default:
                cls._shared_client = client
            logger.info("ChromaDB connected")
        except Exception as e:
            logger.warning(f"ChromaDB unavailable: {e}")
            self._available = False
            if self._is_default:
                cls._shared_unavailable_until = time.monotonic() + _RETRY_INTERVAL

    @property
    def available(self) -> bool:
        return self._available

    # ── 集合管理 ────────────────────────────────────────────────────

    def get_or_create_collection(self, name: str) -> Any | None:
        """返回或创建集合 (使用余弦距离)"""
        if not self._available:
            return None
        try:
            return self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as e:
            logger.warning(f"ChromaDB collection error: {e}")
            return None

    def delete_collection(self, name: str) -> bool:
        if not self._available:
            return False
        try:
            self._client.delete_collection(name)
            return True
        except Exception:
            return False

    # ── 写入 ────────────────────────────────────────────────────────

    def upsert_entities(self, ontology_id: str, entities: list[dict]) -> int:
        """将实体 upsert 到集合 (自动文本嵌入)"""
        if not self._available or not entities:
            return 0
        collection = self.get_or_create_collection(f"ontology_{ontology_id}")
        if not collection:
            return 0

        ids = [e.get("id", str(i)) for i, e in enumerate(entities)]
        documents = [self._entity_to_text(e) for e in entities]
        metadatas = [
            {
                "entity_type": str(e.get("type", "")),
                "name_cn": str(e.get("name_cn", "")),
                "name_en": str(e.get("name_en", "")),
                "confidence": float(e.get("confidence", 0.0)),
            }
            for e in entities
        ]

        try:
            collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
            return len(ids)
        except Exception as e:
            logger.warning(f"ChromaDB upsert error: {e}")
            return 0

    def delete_entities(self, ontology_id: str, entity_ids: list[str]) -> bool:
        """删除指定 ID 的实体"""
        if not self._available:
            return False
        collection = self.get_or_create_collection(f"ontology_{ontology_id}")
        if not collection:
            return False
        try:
            collection.delete(ids=entity_ids)
            return True
        except Exception:
            return False

    # ── 检索 ────────────────────────────────────────────────────────

    def semantic_search(
        self,
        ontology_id: str,
        query: str,
        n_results: int = 10,
        entity_type: str | None = None,
    ) -> list[dict]:
        """语义检索 — ChromaDB 向量相似度搜索"""
        if not self._available:
            return []
        collection = self.get_or_create_collection(f"ontology_{ontology_id}")
        if not collection:
            return []

        kwargs: dict[str, Any] = {
            "query_texts": [query],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if entity_type:
            kwargs["where"] = {"entity_type": entity_type}

        try:
            results = collection.query(**kwargs)
            hits = []
            ids = results.get("ids", [[]])[0]
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for i, eid in enumerate(ids):
                hits.append({
                    "id": eid,
                    "document": docs[i] if i < len(docs) else "",
                    "metadata": metas[i] if i < len(metas) else {},
                    "score": 1.0 - (distances[i] if i < len(distances) else 1.0),
                })
            return hits
        except Exception as e:
            logger.warning(f"ChromaDB search error: {e}")
            return []

    def keyword_search(
        self,
        ontology_id: str,
        keyword: str,
        n_results: int = 20,
    ) -> list[dict]:
        """关键词检索 — 过滤 document 中包含 keyword 的结果"""
        if not self._available:
            return []
        collection = self.get_or_create_collection(f"ontology_{ontology_id}")
        if not collection:
            return []

        try:
            results = collection.query(
                query_texts=[keyword],
                n_results=n_results,
                where_document={"$contains": keyword},
                include=["documents", "metadatas"],
            )
            ids = results.get("ids", [[]])[0]
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            return [
                {"id": ids[i], "document": docs[i], "metadata": metas[i]}
                for i in range(len(ids))
            ]
        except Exception as e:
            logger.warning(f"ChromaDB keyword search error: {e}")
            return []

    def count(self, ontology_id: str) -> int:
        """集合内文档数"""
        if not self._available:
            return 0
        collection = self.get_or_create_collection(f"ontology_{ontology_id}")
        if not collection:
            return 0
        try:
            return collection.count()
        except Exception:
            return 0

    @staticmethod
    def _entity_to_text(entity: dict) -> str:
        """实体 → 嵌入用文本转换"""
        parts = [
            entity.get("name_cn", ""),
            entity.get("name_en", ""),
            entity.get("type", ""),
            entity.get("description", ""),
            json.dumps(entity.get("properties", {}), ensure_ascii=False),
        ]
        return " ".join(p for p in parts if p)


def get_chroma_service() -> ChromaService:
    return ChromaService()
