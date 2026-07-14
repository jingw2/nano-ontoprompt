"""FileConnector 단위 테스트"""
from unittest.mock import MagicMock, patch
import pytest
from app.services.connection.file_connector import FileConnector


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.ensure_bucket.return_value = None
    storage.list_prefix.return_value = ["s3://raw-datasets/uploads/file.csv"]
    storage.get_object.return_value = b"col1,col2\n1,2\n"
    storage.put_bytes.return_value = "s3://raw-datasets/uploads/file.csv"
    return storage


@pytest.fixture
def connector(mock_storage):
    return FileConnector({"prefix": "uploads/"}, storage=mock_storage)


def test_test_connection_success(connector, mock_storage):
    assert connector.test_connection() is True
    mock_storage.ensure_bucket.assert_called_once()


def test_test_connection_failure():
    bad_storage = MagicMock()
    bad_storage.ensure_bucket.side_effect = Exception("No service")
    fc = FileConnector({"prefix": "uploads/"}, storage=bad_storage)
    assert fc.test_connection() is False


def test_list_resources(connector, mock_storage):
    resources = connector.list_resources()
    assert resources == ["s3://raw-datasets/uploads/file.csv"]


def test_pull_sample(connector):
    sample = connector.pull_sample("s3://raw-datasets/file.csv")
    assert isinstance(sample, list)
    assert len(sample) == 1
    assert sample[0]["uri"] == "s3://raw-datasets/file.csv"


def test_pull_full_returns_bytes(connector, mock_storage):
    content = connector.pull_full("s3://raw-datasets/file.csv")
    assert content == b"col1,col2\n1,2\n"


def test_upload_file_returns_uri(connector, mock_storage):
    uri = connector.upload_file("test.csv", b"a,b\n1,2\n")
    assert uri.startswith("s3://")
    mock_storage.put_bytes.assert_called_once()


def test_registry_returns_file_connector():
    from app.services.connection.registry import get_connector
    conn = get_connector("file", {"prefix": "test/"})
    assert isinstance(conn, FileConnector)


def test_registry_unsupported_kind():
    from app.services.connection.registry import get_connector
    with pytest.raises(ValueError, match="Unsupported"):
        get_connector("ftp", {})
