"""SQLConnector 단위 테스트 — 실제 DB 없이 SQLAlchemy mock 사용"""
from unittest.mock import MagicMock, patch
import pytest

import app.services.connection.sql_connector as _sql_mod  # noqa: ensure module loaded


def test_sql_connector_test_connection_success():
    """test_connection이 SELECT 1 성공 시 True를 반환"""
    with patch.object(_sql_mod, "create_engine") as mock_engine_factory:
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_engine_factory.return_value = mock_engine

        from app.services.connection.sql_connector import SQLConnector
        connector = SQLConnector({"connection_string": "postgresql://test/test"})
        result = connector.test_connection()
        assert result is True


def test_sql_connector_test_connection_failure():
    """연결 실패 시 False 반환"""
    with patch.object(_sql_mod, "create_engine") as mock_engine_factory:
        mock_engine_factory.side_effect = Exception("Connection refused")
        from app.services.connection.sql_connector import SQLConnector
        connector = SQLConnector({"connection_string": "mysql://bad/db"})
        assert connector.test_connection() is False


def test_registry_mysql_returns_sql_connector():
    from app.services.connection.registry import CONNECTOR_REGISTRY
    from app.services.connection.sql_connector import SQLConnector
    assert CONNECTOR_REGISTRY["mysql"] is SQLConnector
    assert CONNECTOR_REGISTRY["postgres"] is SQLConnector


def test_get_connector_all_supported_kinds():
    from app.services.connection.registry import get_connector
    for kind in ["file", "mysql", "postgres"]:
        conn = get_connector(kind, {"connection_string": "x", "prefix": "/"})
        assert conn is not None


def test_sql_connector_pull_delta_no_watermark():
    """watermark_column 없으면 pull_full 호출"""
    from app.services.connection.sql_connector import SQLConnector
    connector = SQLConnector({"connection_string": "postgresql://test/test"})
    # pull_delta with no watermark_column should fall back to pull_full
    connector.pull_full = MagicMock(return_value=[{"id": 1}])
    result = connector.pull_delta("orders", since=None)
    connector.pull_full.assert_called_once_with("orders")
    assert result == [{"id": 1}]


def test_sql_connector_engine_lazy_init():
    """엔진은 첫 번째 _get_engine() 호출 시 생성된다"""
    from app.services.connection.sql_connector import SQLConnector
    connector = SQLConnector({"connection_string": "postgresql://test/test"})
    assert connector._engine is None  # 초기화 전


def test_sql_connector_list_resources_calls_inspect():
    """list_resources는 SQLAlchemy inspector를 통해 테이블 목록을 반환"""
    with patch.object(_sql_mod, "create_engine") as mock_engine_factory:
        mock_engine = MagicMock()
        mock_engine_factory.return_value = mock_engine

        with patch.object(_sql_mod, "inspect") as mock_inspect:
            mock_inspector = MagicMock()
            mock_inspector.get_table_names.return_value = ["users", "orders"]
            mock_inspect.return_value = mock_inspector

            from app.services.connection.sql_connector import SQLConnector
            connector = SQLConnector({"connection_string": "postgresql://test/test"})
            tables = connector.list_resources()
            assert tables == ["users", "orders"]
            mock_inspector.get_table_names.assert_called_once()
