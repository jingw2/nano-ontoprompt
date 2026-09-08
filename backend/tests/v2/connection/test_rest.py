"""RestConnector 单元测试"""
from datetime import timedelta
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


def test_rest_delta_passes_cursor_parameter_and_returns_delta_page():
    from app.schemas.refresh import SourceCursor
    from app.services.connection.base import DeltaPage

    conn = make_connector({
        "base_url": "https://api.example.com",
        "cursor_contract": "watermark_primary_key",
        "delta_param": "updated_since",
        "watermark_column": "updated_at",
        "primary_key_column": "id",
        "pagination": {"data_path": "data"},
    })
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [{"id": "101", "updated_at": "2026-08-26T01:00:00Z"}],
        "next_cursor": "next-page",
    }
    mock_session.get.return_value = mock_resp
    conn._session = mock_session

    page = conn.pull_delta(
        "/orders",
        cursor=SourceCursor(
            source_id="source-001", resource="/orders", contract="watermark_primary_key",
            watermark="2026-08-26T00:00:00Z", primary_key="100", opaque_value=None, observed_at=None,
        ),
        overlap_window=timedelta(minutes=1),
    )

    assert isinstance(page, DeltaPage)
    assert page.envelopes[0].primary_key == "101"
    assert page.candidate_cursor.primary_key == "101"
    assert mock_session.get.call_args.kwargs["params"]["updated_since"] == "2026-08-25T23:59:00Z"
