"""文件上传 Connector — 基于 MinIO"""
from __future__ import annotations

import mimetypes
from typing import Any

from app.services.connection.base import ConnectorBase
from app.services.storage_service import StorageService, get_storage_service


class FileConnector(ConnectorBase):
    """
    将本地文件上传作为 Connection 处理的 Connector。
    config 示例: {"bucket": "raw-datasets", "prefix": "uploads/conn-id/"}
    """

    BUCKET = "raw-datasets"

    def __init__(self, config: dict, storage: StorageService | None = None):
        self._config = config
        self._storage = storage or get_storage_service()
        self._prefix = config.get("prefix", "uploads/")

    def test_connection(self) -> bool:
        try:
            self._storage.ensure_bucket(self.BUCKET)
            return True
        except Exception:
            return False

    def list_resources(self) -> list[str]:
        """MinIO prefix 下的文件 URI 列表"""
        return self._storage.list_prefix(self.BUCKET, self._prefix)

    def pull_sample(self, resource: str, limit: int = 100) -> list[dict]:
        """返回文件元信息 (实际解析在 Transform 阶段)"""
        return [{"uri": resource, "type": "file"}]

    def pull_full(self, resource: str) -> bytes:
        """以 bytes 返回文件内容"""
        return self._storage.get_object(resource)

    def upload_file(self, filename: str, data: bytes, content_type: str = "") -> str:
        """上传文件到 MinIO 并返回 URI"""
        if not content_type:
            content_type, _ = mimetypes.guess_type(filename)
            content_type = content_type or "application/octet-stream"
        key = f"{self._prefix}{filename}"
        return self._storage.put_bytes(self.BUCKET, key, data, content_type)
