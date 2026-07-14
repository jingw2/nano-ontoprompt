"""Dataset CRUD + 版本管理服务 (含 DuckDB 预览)"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.v2.dataset import Dataset, DatasetVersion
from app.services.storage_service import StorageService, get_storage_service


class DatasetService:
    def __init__(self, db: Session, storage: StorageService | None = None):
        self._db = db
        self._storage = storage or get_storage_service()

    def create_dataset(self, name: str, kind: str, connection_id: str | None = None) -> Dataset:
        ds = Dataset(name=name, kind=kind, source_connection_id=connection_id)
        self._db.add(ds)
        self._db.commit()
        self._db.refresh(ds)
        return ds

    def create_version(self, dataset_id: str, data: bytes, rowcount: int | None = None) -> DatasetVersion:
        """将数据存入 MinIO 并创建 DatasetVersion"""
        ds = self._db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if not ds:
            raise ValueError(f"Dataset {dataset_id} not found")

        # 确定版本号
        last_ver = self._db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == dataset_id
        ).order_by(DatasetVersion.version_no.desc()).first()
        version_no = (last_ver.version_no + 1) if last_ver else 1

        # 存入 MinIO
        checksum = hashlib.sha256(data[:1024]).hexdigest()[:16]
        key = f"datasets/{dataset_id}/v{version_no}/data.bin"
        uri = self._storage.put_bytes("raw-datasets", key, data)

        ver = DatasetVersion(
            dataset_id=dataset_id,
            version_no=version_no,
            rowcount=rowcount,
            storage_uri=uri,
            checksum=checksum,
        )
        self._db.add(ver)
        ds.latest_version_id = ver.id
        self._db.commit()
        self._db.refresh(ver)
        return ver

    def get_dataset(self, dataset_id: str) -> Dataset | None:
        return self._db.query(Dataset).filter(Dataset.id == dataset_id).first()

    def list_datasets(self, kind: str | None = None) -> list[Dataset]:
        q = self._db.query(Dataset)
        if kind:
            q = q.filter(Dataset.kind == kind)
        return q.all()

    def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        return self._db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == dataset_id
        ).order_by(DatasetVersion.version_no).all()

    def preview(self, dataset_id: str, version_no: int, limit: int = 100) -> list[dict]:
        """CSV/JSON 数据预览。无需 DuckDB, 纯 Python 处理。"""
        ver = self._db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == dataset_id,
            DatasetVersion.version_no == version_no,
        ).first()
        if not ver or not ver.storage_uri:
            return []

        try:
            raw = self._storage.get_object(ver.storage_uri)
            text = raw.decode("utf-8", errors="replace").lstrip("﻿")

            # 检测是否 JSON
            stripped = text.lstrip()
            if stripped.startswith("[") or stripped.startswith("{"):
                import json
                data = json.loads(text)
                if isinstance(data, list):
                    return data[:limit]
                elif isinstance(data, dict):
                    return [data]
                return []

            # 检测是否 Excel (xlsx) 二进制格式
            if raw[:2] == b"PK":
                try:
                    import openpyxl, io
                    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
                    ws = wb.active
                    rows_list = []
                    headers = []
                    for ri, row in enumerate(ws.iter_rows(values_only=True)):
                        if ri == 0:
                            headers = [str(c) if c is not None else f"col_{i}" for i, c in enumerate(row)]
                        else:
                            row_dict = {}
                            for i, val in enumerate(row):
                                if i < len(headers):
                                    row_dict[headers[i]] = val if val is not None else ""
                            rows_list.append(row_dict)
                            if len(rows_list) >= limit:
                                break
                    wb.close()
                    return rows_list
                except Exception:
                    pass

            # 默认 CSV
            import csv, io
            reader = csv.DictReader(io.StringIO(text))
            rows = []
            for i, row in enumerate(reader):
                if i >= limit:
                    break
                rows.append(dict(row))
            return rows
        except Exception:
            return []
