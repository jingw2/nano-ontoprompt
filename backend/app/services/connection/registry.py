"""Connector 注册表 — 按连接类型映射 Connector 类"""
from __future__ import annotations

from app.services.connection.base import ConnectorBase
from app.services.connection.file_connector import FileConnector
from app.services.connection.sql_connector import SQLConnector
from app.services.connection.mongo_connector import MongoConnector
from app.services.connection.rest_connector import RestConnector

CONNECTOR_REGISTRY: dict[str, type[ConnectorBase]] = {
    "file": FileConnector,
    "mysql": SQLConnector,
    "postgres": SQLConnector,
    "mongo": MongoConnector,
    "rest": RestConnector,
}


def get_connector(kind: str, config: dict) -> ConnectorBase:
    """
    根据连接类型和（已解密的）config 返回 Connector 实例。
    不支持的类型抛出 ValueError。
    """
    cls = CONNECTOR_REGISTRY.get(kind)
    if cls is None:
        raise ValueError(
            f"Unsupported connection kind: {kind!r}. Supported: {list(CONNECTOR_REGISTRY.keys())}"
        )
    return cls(config)
