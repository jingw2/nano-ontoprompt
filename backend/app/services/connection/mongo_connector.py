"""MongoDB Connector — 支持 SNAPSHOT 与增量(基于 _id 水位线)"""
from __future__ import annotations
from datetime import timedelta
import logging
from typing import Any
from app.services.connection.base import ConnectorBase
from app.services.connection.base import DeltaPage, records_to_delta_page
from app.schemas.refresh import SourceCursor

logger = logging.getLogger(__name__)
_LEGACY_UNSET = object()


class MongoConnector(ConnectorBase):
    """
    MongoDB 数据源连接器。

    config 示例:
    {
        "uri": "mongodb://user:pass@host:27017/dbname",
        "database": "mydb",
        "collection": "orders"   # 可选, 未指定时 list_resources() 返回所有集合
    }
    """

    def __init__(self, config: dict):
        self._config = config
        self._client = None
        self._db = None

    def _get_db(self):
        """返回 MongoDB 数据库实例 (延迟初始化)"""
        if self._db is None:
            try:
                from pymongo import MongoClient
                self._client = MongoClient(
                    self._config["uri"],
                    serverSelectionTimeoutMS=5000,
                )
                db_name = self._config.get("database", "")
                if not db_name:
                    # 从 URI 解析数据库名
                    db_name = self._config["uri"].split("/")[-1].split("?")[0] or "test"
                self._db = self._client[db_name]
            except ImportError:
                raise RuntimeError("pymongo 未安装, 请执行 pip install pymongo")
        return self._db

    def test_connection(self) -> bool:
        """连接测试 — 成功返回 True, 失败返回 False (不抛异常)"""
        try:
            db = self._get_db()
            db.list_collection_names()
            return True
        except Exception as e:
            logger.warning(f"MongoDB 连接测试失败: {e}")
            return False

    def list_resources(self) -> list[str]:
        """返回数据库的所有集合名"""
        try:
            return self._get_db().list_collection_names()
        except Exception as e:
            logger.warning(f"MongoDB list_resources 失败: {e}")
            return []

    def pull_sample(self, resource: str, limit: int = 100) -> list[dict]:
        """从集合中查询样本数据"""
        try:
            collection = self._get_db()[resource]
            docs = list(collection.find({}, {"_id": 0}).limit(limit))
            return docs
        except Exception as e:
            logger.warning(f"MongoDB pull_sample 失败: {e}")
            return []

    def pull_full(self, resource: str) -> list[dict]:
        """查询全量数据 (排除 _id 字段, 避免序列化问题)"""
        try:
            collection = self._get_db()[resource]
            docs = []
            for doc in collection.find({}, {"_id": 0}):
                docs.append(doc)
            return docs
        except Exception as e:
            logger.warning(f"MongoDB pull_full 失败: {e}")
            return []

    def pull_delta(
        self,
        resource: str,
        *,
        cursor: SourceCursor | None = None,
        overlap_window: timedelta = timedelta(0),
        since: str | None | object = _LEGACY_UNSET,
    ) -> DeltaPage | list[dict]:
        """Incremental query using Mongo's configured opaque/ObjectId cursor."""
        contract = self._config.get("cursor_contract", "opaque_source_cursor")
        if since is not _LEGACY_UNSET:
            if not since:
                return self.pull_full(resource)
            try:
                from bson import ObjectId
                collection = self._get_db()[resource]
                docs = []
                for doc in collection.find({"_id": {"$gt": ObjectId(since)}}, {"_id": 0}):
                    docs.append(doc)
                return docs
            except Exception as e:
                logger.warning(f"MongoDB pull_delta 失败: {e}")
                return self.pull_full(resource)

        if contract != "opaque_source_cursor":
            rows = self.pull_full(resource)
            return records_to_delta_page(
                rows, source_id=self._config.get("source_id", ""), resource=resource,
                contract=contract, cursor=cursor, primary_key_field="_id",
                cursor_field="_id", cursor_outcome="unchanged",
            )
        try:
            from bson import ObjectId
            collection = self._get_db()[resource]
            query = {}
            if cursor is not None and cursor.opaque_value is not None:
                query = {"_id": {"$gt": ObjectId(str(cursor.opaque_value))}}
            docs = list(collection.find(query))
            return records_to_delta_page(
                docs, source_id=self._config.get("source_id", ""), resource=resource,
                contract=contract, cursor=cursor, primary_key_field="_id",
                cursor_field="_id",
            )
        except Exception as e:
            logger.warning(f"MongoDB pull_delta 失败: {e}")
            raise
