"""
StorageService 단위 테스트.
실제 MinIO 없이 unittest.mock으로 Minio 클라이언트를 모킹합니다.
"""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.services.storage_service import StorageService, BUCKETS


@pytest.fixture
def mock_minio():
    with patch("app.services.storage_service.Minio") as MockMinio:
        instance = MockMinio.return_value
        instance.bucket_exists.return_value = True
        yield instance


@pytest.fixture
def storage(mock_minio):
    svc = StorageService(
        endpoint="localhost:9000",
        access_key="test",
        secret_key="test",
        secure=False,
    )
    return svc


# ── 1. 버킷 초기화 ────────────────────────────────────────────
def test_ensure_bucket_existing(storage, mock_minio):
    """이미 존재하는 버킷은 make_bucket을 호출하지 않는다."""
    mock_minio.bucket_exists.return_value = True
    storage.ensure_bucket("raw-datasets")
    mock_minio.make_bucket.assert_not_called()


def test_ensure_bucket_new(storage, mock_minio):
    """존재하지 않는 버킷은 make_bucket을 호출한다."""
    mock_minio.bucket_exists.return_value = False
    storage.ensure_bucket("new-bucket")
    mock_minio.make_bucket.assert_called_once_with("new-bucket")


def test_ensure_default_buckets_creates_4(storage, mock_minio):
    """4개 기본 버킷을 초기화할 때 각 버킷 존재 여부를 확인한다."""
    mock_minio.bucket_exists.return_value = True
    storage.ensure_default_buckets()
    assert mock_minio.bucket_exists.call_count == len(BUCKETS)


# ── 2. 업로드 ─────────────────────────────────────────────────
def test_put_object_returns_s3_uri(storage, mock_minio):
    """put_object는 s3://bucket/key 형태의 URI를 반환한다."""
    data = io.BytesIO(b"hello world")
    uri = storage.put_object("raw-datasets", "test/data.csv", data, "text/csv", 11)
    assert uri == "s3://raw-datasets/test/data.csv"
    mock_minio.put_object.assert_called_once()


def test_put_bytes_returns_s3_uri(storage, mock_minio):
    """put_bytes는 bytes를 받아 s3:// URI를 반환한다."""
    uri = storage.put_bytes("media", "file.pdf", b"%PDF", "application/pdf")
    assert uri == "s3://media/file.pdf"


# ── 3. 다운로드 ───────────────────────────────────────────────
def test_get_object_returns_bytes(storage, mock_minio):
    """get_object는 오브젝트 내용을 bytes로 반환한다."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"content"
    mock_minio.get_object.return_value = mock_resp
    result = storage.get_object("s3://raw-datasets/test.csv")
    assert result == b"content"


# ── 4. URI 파싱 ───────────────────────────────────────────────
def test_parse_uri_valid():
    bucket, key = StorageService._parse_uri("s3://my-bucket/path/to/file.csv")
    assert bucket == "my-bucket"
    assert key == "path/to/file.csv"


def test_parse_uri_invalid_scheme():
    with pytest.raises(ValueError, match="Invalid storage URI"):
        StorageService._parse_uri("http://bucket/key")


def test_parse_uri_missing_key():
    with pytest.raises(ValueError, match="Invalid storage URI"):
        StorageService._parse_uri("s3://bucket/")


# ── 5. 삭제 ──────────────────────────────────────────────────
def test_delete_object_calls_remove(storage, mock_minio):
    storage.delete_object("s3://raw-datasets/old.csv")
    mock_minio.remove_object.assert_called_once_with("raw-datasets", "old.csv")


# ── 6. 목록 ──────────────────────────────────────────────────
def test_list_prefix_returns_uris(storage, mock_minio):
    obj1 = MagicMock()
    obj1.object_name = "prefix/file1.csv"
    obj2 = MagicMock()
    obj2.object_name = "prefix/file2.csv"
    mock_minio.list_objects.return_value = [obj1, obj2]
    uris = storage.list_prefix("raw-datasets", "prefix/")
    assert uris == ["s3://raw-datasets/prefix/file1.csv", "s3://raw-datasets/prefix/file2.csv"]


# ── 7. Presigned URL ─────────────────────────────────────────
def test_presigned_get_returns_url(storage, mock_minio):
    mock_minio.presigned_get_object.return_value = "http://minio/bucket/key?sig=xxx"
    url = storage.presigned_get("s3://media/report.pdf", 1800)
    assert url.startswith("http://")
    mock_minio.presigned_get_object.assert_called_once()


# ── 8. 바이너리 roundtrip ─────────────────────────────────────
def test_binary_roundtrip(storage, mock_minio):
    """put_bytes 후 get_object가 동일 bytes를 반환하는 흐름 검증"""
    content = b"\x89PNG\r\n\x1a\n"  # PNG magic bytes
    mock_resp = MagicMock()
    mock_resp.read.return_value = content
    mock_minio.get_object.return_value = mock_resp

    uri = storage.put_bytes("media", "img.png", content, "image/png")
    retrieved = storage.get_object(uri)
    assert retrieved == content
