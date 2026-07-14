"""
基于 MinIO 的对象存储服务。
桶: raw-datasets, curated-datasets, media, intermediate
"""
from __future__ import annotations

import io
import os
from typing import BinaryIO

import logging

try:
    from minio import Minio
    from minio.error import S3Error
    _MINIO_AVAILABLE = True
except ImportError:
    Minio = None  # type: ignore
    S3Error = Exception  # type: ignore
    _MINIO_AVAILABLE = False

from app.config import settings

logger = logging.getLogger(__name__)

BUCKETS = ["raw-datasets", "curated-datasets", "media", "intermediate"]


class StorageService:
    """MinIO 对象存储, 含本地文件系统回退。

    使用默认配置时全进程共享同一个客户端; 连接失败后 60 秒内不再重试,
    避免每次实例化都触发 urllib3 多次重连 (此前是测试与请求变慢的主因)。
    """

    _shared_client = None
    _shared_unavailable_until: float = 0.0
    _RETRY_INTERVAL = 60.0

    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        secure: bool | None = None,
    ):
        import time
        self._available = False
        self._client = None
        if not _MINIO_AVAILABLE:
            logger.warning("MinIO client not installed — storage unavailable")
            return

        is_default = endpoint is None and access_key is None and secret_key is None and secure is None
        cls = StorageService
        if is_default:
            if cls._shared_client is not None:
                self._client = cls._shared_client
                self._available = True
                return
            if time.monotonic() < cls._shared_unavailable_until:
                return
        try:
            client = Minio(
                endpoint or settings.minio_endpoint,
                access_key=access_key or settings.minio_access_key,
                secret_key=secret_key or settings.minio_secret_key,
                secure=secure if secure is not None else settings.minio_use_ssl,
            )
            client.list_buckets()  # 连接验证
            self._client = client
            self._available = True
            if is_default:
                cls._shared_client = client
            logger.info("MinIO connected")
        except Exception as e:
            logger.warning(f"MinIO unavailable: {e}")
            self._available = False
            if is_default:
                cls._shared_unavailable_until = time.monotonic() + cls._RETRY_INTERVAL

    # ── 本地文件系统 fallback ─────────────────────────────────────
    _LOCAL_BASE = os.path.join(os.path.dirname(__file__), "../../../../storage")

    @property
    def available(self) -> bool:
        return True  # 本地 fallback 始终可用

    def _require_available(self):
        pass  # 本地 fallback 不需要 MinIO

    def _local_path(self, bucket: str, key: str) -> str:
        p = os.path.join(self._LOCAL_BASE, bucket, key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def ensure_bucket(self, bucket: str) -> None:
        """桶不存在则创建。"""
        self._require_available()
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)

    def ensure_default_buckets(self) -> None:
        """初始化全部 4 个默认桶。"""
        for b in BUCKETS:
            self.ensure_bucket(b)

    def put_object(
        self,
        bucket: str,
        key: str,
        data: BinaryIO,
        content_type: str = "application/octet-stream",
        length: int = -1,
    ) -> str:
        """上传对象并返回 URI(s3://bucket/key)。"""
        self.ensure_bucket(bucket)
        # minio-py 在 length=-1 时使用 chunked read
        self._client.put_object(
            bucket, key, data, length=length, content_type=content_type
        )
        return f"s3://{bucket}/{key}"

    def put_bytes(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """上传 bytes。MinIO 未连接时回退本地文件。"""
        if self._available and self._client:
            return self.put_object(bucket, key, io.BytesIO(data), content_type, length=len(data))
        # 本地回退
        local = self._local_path(bucket, key)
        with open(local, "wb") as f:
            f.write(data)
        return f"s3://{bucket}/{key}"

    def get_object(self, uri: str) -> bytes:
        """按 s3://bucket/key URI 下载对象。含本地回退。"""
        bucket, key = self._parse_uri(uri)
        if self._available and self._client:
            resp = self._client.get_object(bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()
        # 本地回退
        local = self._local_path(bucket, key)
        if os.path.exists(local):
            with open(local, "rb") as f:
                return f.read()
        raise FileNotFoundError(f"Object not found locally: {uri}")

    def get_stream(self, uri: str) -> BinaryIO:
        """按 s3://bucket/key URI 返回流。"""
        bucket, key = self._parse_uri(uri)
        return self._client.get_object(bucket, key)

    def presigned_get(self, uri: str, expires_seconds: int = 3600) -> str:
        """生成下载用 presigned URL。"""
        from datetime import timedelta
        bucket, key = self._parse_uri(uri)
        url = self._client.presigned_get_object(
            bucket, key, expires=timedelta(seconds=expires_seconds)
        )
        return url

    def delete_object(self, uri: str) -> None:
        """删除对象。"""
        bucket, key = self._parse_uri(uri)
        self._client.remove_object(bucket, key)

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        """返回 prefix 下的对象键列表。"""
        objects = self._client.list_objects(bucket, prefix=prefix, recursive=True)
        return [f"s3://{bucket}/{obj.object_name}" for obj in objects]

    def object_exists(self, uri: str) -> bool:
        """检查对象是否存在。MinIO 不可用时回退本地文件系统。"""
        bucket, key = self._parse_uri(uri)
        if self._available and self._client:
            try:
                self._client.stat_object(bucket, key)
                return True
            except S3Error:
                return False
        return os.path.exists(os.path.join(self._LOCAL_BASE, bucket, key))

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str]:
        """s3://bucket/key → (bucket, key)"""
        if not uri.startswith("s3://"):
            raise ValueError(f"Invalid storage URI: {uri!r}. Expected s3://bucket/key")
        path = uri[5:]
        bucket, _, key = path.partition("/")
        if not bucket or not key:
            raise ValueError(f"Invalid storage URI: {uri!r}")
        return bucket, key


# 单例实例 (供 FastAPI 依赖注入使用)
_storage_service: StorageService | None = None


def get_storage_service() -> StorageService:
    global _storage_service
    if _storage_service is None:
        _storage_service = StorageService()
    return _storage_service
