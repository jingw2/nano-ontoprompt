"""RestConnector 单元测试"""
import pytest
from unittest.mock import MagicMock


def make_connector(config=None):
    from app.services.connection.rest_connector import RestConnector
    return RestConnector(config or {
        "base_url": "https://api.example.com",
        "endpoints": ["/orders", "/customers"],
        "pagination": {"data_path": "data"},
    })


def test_rest_list_resources():
    conn = make_connector()
    assert conn.list_resources() == ["/orders", "/customers"]


def test_rest_extract_records_list():
    conn = make_connector()
    data = [{"id": 1}, {"id": 2}]
    assert conn._extract_records(data) == data


def test_rest_extract_records_dict_data_path():
    conn = make_connector({"base_url": "x", "pagination": {"data_path": "data"}})
    data = {"data": [{"id": 1}], "total": 1}
    assert conn._extract_records(data) == [{"id": 1}]


def test_rest_extract_records_results_key():
    conn = make_connector({"base_url": "x", "pagination": {}})
    data = {"results": [{"id": 1}, {"id": 2}]}
    assert conn._extract_records(data) == [{"id": 1}, {"id": 2}]


def test_rest_extract_records_empty():
    conn = make_connector()
    assert conn._extract_records({}) == []


def test_rest_pull_sample_success():
    conn = make_connector()
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"id": 1}, {"id": 2}]}
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp
    conn._session = mock_session
    result = conn.pull_sample("/orders", limit=2)
    assert len(result) == 2


def test_rest_test_connection_success():
    conn = make_connector()
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_session.get.return_value = mock_resp
    conn._session = mock_session
    assert conn.test_connection() is True


def test_rest_test_connection_failure():
    conn = make_connector()
    mock_session = MagicMock()
    mock_session.get.side_effect = Exception("Connection refused")
    conn._session = mock_session
    assert conn.test_connection() is False


def test_rest_registry_registered():
    from app.services.connection.registry import CONNECTOR_REGISTRY
    assert "rest" in CONNECTOR_REGISTRY
