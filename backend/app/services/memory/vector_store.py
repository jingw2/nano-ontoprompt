"""Chroma-backed memory embedding store (P6B-2b, Section 11).

One Chroma collection per security domain (memories are always scoped to a
security domain, agent, and user; namespacing the collection by security
domain — the coarsest of the three — keeps collection count bounded while
still letting per-agent/per-user scoping happen at the SQL layer before any
Chroma hit is trusted, matching this codebase's existing candidate-collection
pattern in app/services/indexes/release_aware.py).

Uses Chroma's own default embedding function (this codebase has no other
embedding-model integration anywhere) via the existing general-purpose
ChromaService wrapper. MEMORY_EMBEDDING_MODEL_VERSION is the pinned
identifier this plan's vector-outbox consumer stamps onto
agent_memories.embedding_model_version on successful upsert; bumping it in
a future plan (e.g. switching embedding models) is what "stale" recall
detection is for.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MEMORY_EMBEDDING_MODEL_VERSION = "memory-embed-chroma-default-v1"

_COLLECTION_PREFIX = "agent_memory_"


def _service():
    from app.services.v2.vector.chroma_service import ChromaService
    return ChromaService()


def is_available() -> bool:
    return _service().available


def memory_collection_name(security_domain_id: str) -> str:
    return f"{_COLLECTION_PREFIX}{security_domain_id}"


def upsert_memory_embedding(memory_id: str, agent_id: str, user_id: str,
                            security_domain_id: str, display_text: str) -> bool:
    service = _service()
    if not service.available:
        return False
    collection = service.get_or_create_collection(memory_collection_name(security_domain_id))
    if not collection:
        return False
    try:
        collection.upsert(ids=[memory_id], documents=[display_text],
                          metadatas=[{"agent_id": agent_id, "user_id": user_id}])
        return True
    except Exception as e:
        logger.warning("memory embedding upsert failed for %s: %s", memory_id, e)
        return False


def delete_memory_embedding(memory_id: str, security_domain_id: str) -> bool:
    service = _service()
    if not service.available:
        return False
    collection = service.get_or_create_collection(memory_collection_name(security_domain_id))
    if not collection:
        return False
    try:
        collection.delete(ids=[memory_id])
        return True
    except Exception as e:
        logger.warning("memory embedding delete failed for %s: %s", memory_id, e)
        return False


def query_similar(security_domain_id: str, agent_id: str, user_id: str, query_text: str,
                  n_results: int) -> list[dict[str, Any]]:
    service = _service()
    if not service.available or n_results <= 0:
        return []
    collection = service.get_or_create_collection(memory_collection_name(security_domain_id))
    if not collection:
        return []
    try:
        results = collection.query(
            query_texts=[query_text], n_results=n_results, include=["distances"],
            where={"$and": [{"agent_id": agent_id}, {"user_id": user_id}]},
        )
    except Exception as e:
        logger.warning("memory embedding query failed for domain %s: %s", security_domain_id, e)
        return []
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]
    hits = []
    for i, memory_id in enumerate(ids):
        distance = distances[i] if i < len(distances) else 2.0
        # collection uses cosine space (ChromaService.get_or_create_collection
        # pins metadata={"hnsw:space": "cosine"}), so cosine distance is
        # exactly 1 - cosine_similarity — recover the raw similarity here.
        hits.append({"id": memory_id, "cosine": 1.0 - distance})
    return hits
