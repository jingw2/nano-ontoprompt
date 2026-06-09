"""v2 Search API — 키워드/시맨틱 통합 검색"""
from __future__ import annotations
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from app.deps import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


class SearchRequest(BaseModel):
    query: str
    mode: str = "keyword"  # keyword | semantic
    entity_type: str | None = None
    n_results: int = 10


@router.get("/{ontology_id}/search/keyword")
def keyword_search(
    ontology_id: str,
    q: str = Query(..., description="검색어"),
    n: int = Query(20, description="결과 수"),
):
    """키워드 검색"""
    from app.services.v2.vector.chroma_service import ChromaService
    svc = ChromaService()
    if not svc.available:
        return {"results": [], "chroma_available": False}
    results = svc.keyword_search(ontology_id, q, n_results=n)
    return {"results": results, "chroma_available": True, "query": q}


@router.get("/{ontology_id}/search/semantic")
def semantic_search(
    ontology_id: str,
    q: str = Query(..., description="검색어"),
    n: int = Query(10, description="결과 수"),
    entity_type: str | None = Query(None, description="엔티티 유형 필터"),
):
    """시맨틱 검색 (벡터 유사도)"""
    from app.services.v2.vector.chroma_service import ChromaService
    svc = ChromaService()
    if not svc.available:
        return {"results": [], "chroma_available": False}
    results = svc.semantic_search(ontology_id, q, n_results=n, entity_type=entity_type)
    return {"results": results, "chroma_available": True, "query": q}


@router.post("/{ontology_id}/search")
def unified_search(ontology_id: str, body: SearchRequest):
    """통합 검색 엔드포인트"""
    from app.services.v2.vector.chroma_service import ChromaService
    svc = ChromaService()
    if not svc.available:
        return {"results": [], "chroma_available": False, "mode": body.mode}

    if body.mode == "semantic":
        results = svc.semantic_search(
            ontology_id, body.query,
            n_results=body.n_results,
            entity_type=body.entity_type,
        )
    else:
        results = svc.keyword_search(ontology_id, body.query, n_results=body.n_results)

    return {"results": results, "chroma_available": True, "mode": body.mode}
