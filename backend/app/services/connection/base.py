"""Connector 抽象基类"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class ConnectorBase(ABC):
    """所有 Connector 必须实现的接口"""

    @abstractmethod
    def test_connection(self) -> bool:
        """连接测试。成功返回 True，失败返回 False 或抛异常。"""
        ...

    @abstractmethod
    def list_resources(self) -> list[str]:
        """可用资源列表 (表名、集合名、端点等)。"""
        ...

    @abstractmethod
    def pull_sample(self, resource: str, limit: int = 100) -> list[dict]:
        """查询样本数据 (最多 limit 行)。"""
        ...

    @abstractmethod
    def pull_full(self, resource: str) -> Any:
        """返回全量数据。大数据量可返回生成器或文件路径。"""
        ...

    def pull_delta(self, resource: str, since: str | None = None) -> Any:
        """增量数据查询。默认实现与 pull_full 相同。"""
        return self.pull_full(resource)
