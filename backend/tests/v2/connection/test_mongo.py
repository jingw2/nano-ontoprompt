"""MongoConnector 单元测试（完全 Mock，不需要真实 MongoDB）"""
import pytest
from unittest.mock import MagicMock, patch


def test_mongo_test_connection_success():
    """连接成功时返回 True"""
    from app.services.connection.mongo_connector import MongoConnector
    conn = MongoConnector({"uri": "mongodb://localhost/testdb", "database": "testdb"})
    mock_db = MagicMock()
    mock_db.list_collection_names.return_value = ["orders", "customers"]
    conn._db = mock_db
    assert conn.test_connection() is True


def test_mongo_test_connection_failure():
    """连接失败时返回 False（不抛出异常）"""
    from app.services.connection.mongo_connector import MongoConnector
    conn = MongoConnector({"uri": "mongodb://bad:99999/db", "database": "db"})
    # _db 未注入，直接调用会尝试连接失败
    result = conn.test_connection()
    assert result is False


def test_mongo_list_resources():
    """list_resources 返回集合名称列表"""
    from app.services.connection.mongo_connector import MongoConnector
    conn = MongoConnector({"uri": "mongodb://localhost/testdb", "database": "testdb"})
    mock_db = MagicMock()
    mock_db.list_collection_names.return_value = ["orders", "customers", "products"]
    conn._db = mock_db
    result = conn.list_resources()
    assert result == ["orders", "customers", "products"]


def test_mongo_pull_sample():
    """pull_sample 返回指定数量的文档"""
    from app.services.connection.mongo_connector import MongoConnector
    conn = MongoConnector({"uri": "mongodb://localhost/testdb", "database": "testdb"})
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_collection.find.return_value.limit.return_value = [
        {"order_id": "ORD-001", "amount": 100},
        {"order_id": "ORD-002", "amount": 200},
    ]
    mock_db.__getitem__ = MagicMock(return_value=mock_collection)
    conn._db = mock_db
    result = conn.pull_sample("orders", limit=2)
    assert len(result) == 2
    assert result[0]["order_id"] == "ORD-001"


def test_mongo_pull_full():
    """pull_full 返回所有文档"""
    from app.services.connection.mongo_connector import MongoConnector
    conn = MongoConnector({"uri": "mongodb://localhost/testdb", "database": "testdb"})
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_collection.find.return_value = [
        {"id": "1", "name": "A"},
        {"id": "2", "name": "B"},
    ]
    mock_db.__getitem__ = MagicMock(return_value=mock_collection)
    conn._db = mock_db
    result = conn.pull_full("items")
    assert len(result) == 2


def test_mongo_registry_registered():
    """registry 中包含 mongo"""
    from app.services.connection.registry import CONNECTOR_REGISTRY
    assert "mongo" in CONNECTOR_REGISTRY
