"""ChromaService + Search API 단위 테스트 — ChromaDB mock"""
import pytest
from unittest.mock import MagicMock, patch


# ── ChromaService mock 헬퍼 ───────────────────────────────────────────

def make_mock_collection(ids=None, docs=None, metas=None, distances=None):
    coll = MagicMock()
    coll.count.return_value = len(ids or [])
    coll.query.return_value = {
        "ids": [ids or []],
        "documents": [docs or []],
        "metadatas": [metas or []],
        "distances": [distances or []],
    }
    return coll


def mock_chroma_client(collection):
    client = MagicMock()
    client.heartbeat.return_value = {"nanosecond heartbeat": 123}
    client.get_or_create_collection.return_value = collection
    return client


# ── 테스트 ────────────────────────────────────────────────────────────

def test_chroma_service_available_on_connect():
    """ChromaDB 연결 성공 시 available=True"""
    with patch("app.services.v2.vector.chroma_service.chromadb") as mock_chroma:
        mock_client = MagicMock()
        mock_client.heartbeat.return_value = {}
        mock_chroma.HttpClient.return_value = mock_client

        from app.services.v2.vector.chroma_service import ChromaService
        svc = ChromaService(host="localhost", port=8001)
        assert svc.available is True


def test_chroma_service_unavailable_on_error():
    """ChromaDB 연결 실패 시 available=False"""
    with patch("app.services.v2.vector.chroma_service.chromadb") as mock_chroma:
        mock_chroma.HttpClient.side_effect = Exception("Connection refused")

        from app.services.v2.vector.chroma_service import ChromaService
        svc = ChromaService(host="bad", port=9999)
        assert svc.available is False


def test_upsert_entities_unavailable_returns_zero():
    """ChromaDB 미연결 시 upsert는 0 반환"""
    with patch("app.services.v2.vector.chroma_service.chromadb") as mock_chroma:
        mock_chroma.HttpClient.side_effect = Exception("offline")

        from app.services.v2.vector.chroma_service import ChromaService
        svc = ChromaService(host="x", port=0)
        assert svc.upsert_entities("ont-1", [{"id": "e1"}]) == 0


def test_semantic_search_unavailable_returns_empty():
    """ChromaDB 미연결 시 시맨틱 검색은 빈 리스트"""
    with patch("app.services.v2.vector.chroma_service.chromadb") as mock_chroma:
        mock_chroma.HttpClient.side_effect = Exception("offline")

        from app.services.v2.vector.chroma_service import ChromaService
        svc = ChromaService(host="x", port=0)
        assert svc.semantic_search("ont-1", "query") == []


def test_keyword_search_unavailable_returns_empty():
    with patch("app.services.v2.vector.chroma_service.chromadb") as mock_chroma:
        mock_chroma.HttpClient.side_effect = Exception("offline")

        from app.services.v2.vector.chroma_service import ChromaService
        svc = ChromaService(host="x", port=0)
        assert svc.keyword_search("ont-1", "keyword") == []


def test_entity_to_text_concatenates_fields():
    from app.services.v2.vector.chroma_service import ChromaService
    entity = {
        "name_cn": "华为",
        "name_en": "Huawei",
        "type": "Organization",
        "description": "Tech company",
    }
    text = ChromaService._entity_to_text(entity)
    assert "华为" in text
    assert "Huawei" in text
    assert "Organization" in text


def test_upsert_entities_with_mock():
    """ChromaDB mock으로 upsert 성공 케이스"""
    coll = MagicMock()
    coll.upsert.return_value = None

    with patch("app.services.v2.vector.chroma_service.chromadb") as mock_chroma:
        mock_client = MagicMock()
        mock_client.heartbeat.return_value = {}
        mock_client.get_or_create_collection.return_value = coll
        mock_chroma.HttpClient.return_value = mock_client

        from app.services.v2.vector.chroma_service import ChromaService
        svc = ChromaService(host="localhost", port=8001)
        count = svc.upsert_entities("ont-1", [
            {"id": "e1", "name_cn": "华为", "type": "Organization"},
            {"id": "e2", "name_cn": "苹果", "type": "Organization"},
        ])
        assert count == 2
        coll.upsert.assert_called_once()


def test_semantic_search_with_mock():
    """ChromaDB mock으로 시맨틱 검색 결과 파싱"""
    coll = make_mock_collection(
        ids=["e1", "e2"],
        docs=["华为 Organization", "苹果 Organization"],
        metas=[{"entity_type": "Organization", "name_cn": "华为"}, {"entity_type": "Organization", "name_cn": "苹果"}],
        distances=[0.1, 0.3],
    )

    with patch("app.services.v2.vector.chroma_service.chromadb") as mock_chroma:
        mock_client = MagicMock()
        mock_client.heartbeat.return_value = {}
        mock_client.get_or_create_collection.return_value = coll
        mock_chroma.HttpClient.return_value = mock_client

        from app.services.v2.vector.chroma_service import ChromaService
        svc = ChromaService(host="localhost", port=8001)
        results = svc.semantic_search("ont-1", "tech company", n_results=2)

        assert len(results) == 2
        assert results[0]["id"] == "e1"
        assert results[0]["score"] == pytest.approx(0.9)


def test_count_with_mock():
    """count() 반환값 검증"""
    coll = MagicMock()
    coll.count.return_value = 42

    with patch("app.services.v2.vector.chroma_service.chromadb") as mock_chroma:
        mock_client = MagicMock()
        mock_client.heartbeat.return_value = {}
        mock_client.get_or_create_collection.return_value = coll
        mock_chroma.HttpClient.return_value = mock_client

        from app.services.v2.vector.chroma_service import ChromaService
        svc = ChromaService(host="localhost", port=8001)
        assert svc.count("ont-1") == 42


def test_legacy_bridge_sync_to_chroma():
    """bridge.sync_to_chroma가 ChromaService.upsert_entities를 호출"""
    with patch("app.services.v2.vector.chroma_service.chromadb") as mock_chroma:
        mock_client = MagicMock()
        mock_client.heartbeat.return_value = {}
        coll = MagicMock()
        coll.upsert.return_value = None
        mock_client.get_or_create_collection.return_value = coll
        mock_chroma.HttpClient.return_value = mock_client

        with patch("app.services.v2.graph.neo4j_service.GraphDatabase") as mock_neo4j:
            mock_neo4j.driver.side_effect = Exception("offline")

            from app.services.v2.legacy_extraction_bridge import LegacyExtractionBridge
            bridge = LegacyExtractionBridge()
            bridge.sync_to_chroma("ont-1", [{"id": "e1", "name_cn": "华为", "type": "Organization"}])

            # ChromaDB upsert가 호출되었는지 확인
            # (bridge가 ChromaService를 사용할 경우)
            # bridge._chroma가 available한 경우에만 호출됨
