"""
v1 本地文件存储 ↔ MinIO 兼容适配器。
用于将 v1 代码存储在本地路径的文件同步到 MinIO。
"""
from __future__ import annotations

import os
from pathlib import Path

from app.config import settings
from app.services.storage_service import StorageService, get_storage_service


class LegacyFileAdapter:
    """将 v1 本地 uploads/ 文件复制到 MinIO media 桶。"""

    BUCKET = "media"

    def __init__(self, storage: StorageService | None = None):
        self._storage = storage or get_storage_service()

    def upload_from_local(self, local_path: str, key_prefix: str = "legacy") -> str:
        """
        将本地文件上传到 MinIO 并返回 URI。
        key: {key_prefix}/{filename}
        """
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        key = f"{key_prefix}/{path.name}"
        content_type = self._guess_content_type(path.suffix)

        with open(local_path, "rb") as f:
            uri = self._storage.put_object(
                self.BUCKET, key, f, content_type=content_type
            )
        return uri

    def get_local_path(self, filename: str) -> str:
        """返回 v1 上传目录中文件的完整路径。"""
        return os.path.join(settings.uploads_dir, filename)

    @staticmethod
    def _guess_content_type(suffix: str) -> str:
        mapping = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".csv": "text/csv",
            ".json": "application/json",
            ".md": "text/markdown",
            ".txt": "text/plain",
        }
        return mapping.get(suffix.lower(), "application/octet-stream")
