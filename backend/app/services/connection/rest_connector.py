"""REST API Connector — 支持分页与增量(since 参数)"""
from __future__ import annotations
import logging
from typing import Any
from app.services.connection.base import ConnectorBase

logger = logging.getLogger(__name__)


class RestConnector(ConnectorBase):
    """
    REST API 数据源连接器。

    config 示例:
    {
        "base_url": "https://api.example.com/v1",
        "endpoints": ["/orders", "/customers"],   # list_resources() 返回该列表
        "auth": {
            "type": "bearer",   # bearer | basic | api_key
            "token": "xxx"      # bearer 令牌
        },
        "params": {"page_size": 100},     # 附加到所有请求的公共参数
        "pagination": {
            "type": "page",     # page | cursor | offset (当前实现 page)
            "page_param": "page",
            "size_param": "page_size",
            "data_path": "data"   # JSON 响应中数据数组的字段名 (如 "data", "results")
        },
        "delta_param": "since"    # 增量参数名, GET 请求附加 ?since=<timestamp>
    }
    """

    def __init__(self, config: dict):
        self._config = config
        self._session = None

    def _get_session(self):
        """返回 httpx 会话实例 (延迟初始化)"""
        if self._session is None:
            try:
                import httpx
            except ImportError:
                raise RuntimeError("httpx 未安装, 请执行 pip install httpx")
            auth_cfg = self._config.get("auth", {})
            headers = {}
            if auth_cfg.get("type") == "bearer":
                headers["Authorization"] = f"Bearer {auth_cfg.get('token', '')}"
            elif auth_cfg.get("type") == "api_key":
                headers[auth_cfg.get("header", "X-API-Key")] = auth_cfg.get("token", "")
            self._session = httpx.Client(
                base_url=self._config.get("base_url", ""),
                headers=headers,
                timeout=30.0,
            )
        return self._session

    def test_connection(self) -> bool:
        """连接测试 — 请求第一个端点检查状态"""
        endpoints = self._config.get("endpoints", [])
        if not endpoints:
            return False
        try:
            resp = self._get_session().get(endpoints[0], params={"page": 1, "page_size": 1})
            return resp.status_code < 400
        except Exception as e:
            logger.warning(f"REST 连接测试失败: {e}")
            return False

    def list_resources(self) -> list[str]:
        """返回 config 中定义的端点列表"""
        return self._config.get("endpoints", [])

    def pull_sample(self, resource: str, limit: int = 100) -> list[dict]:
        """从端点查询样本数据"""
        try:
            params = dict(self._config.get("params", {}))
            params.update({"page": 1, "page_size": min(limit, 100)})
            resp = self._get_session().get(resource, params=params)
            resp.raise_for_status()
            return self._extract_records(resp.json())[:limit]
        except Exception as e:
            logger.warning(f"REST pull_sample 失败: {e}")
            return []

    def pull_full(self, resource: str) -> list[dict]:
        """通过分页查询全量数据"""
        pagination = self._config.get("pagination", {})
        page_param = pagination.get("page_param", "page")
        size_param = pagination.get("size_param", "page_size")

        all_records = []
        page = 1
        base_params = dict(self._config.get("params", {}))

        try:
            session = self._get_session()
            while True:
                params = {**base_params, page_param: page, size_param: 100}
                resp = session.get(resource, params=params)
                resp.raise_for_status()
                data = resp.json()
                records = self._extract_records(data)
                if not records:
                    break
                all_records.extend(records)
                # 检查是否存在下一页
                if isinstance(data, dict):
                    if not data.get("next") and len(records) < 100:
                        break
                else:
                    break
                page += 1
                if page > 100:  # 安全上限
                    break
        except Exception as e:
            logger.warning(f"REST pull_full 失败: {e}")

        return all_records

    def pull_delta(self, resource: str, since: str | None = None) -> list[dict]:
        """增量查询: 将 since 参数加入查询串后请求"""
        if not since:
            return self.pull_full(resource)
        delta_param = self._config.get("delta_param", "since")
        try:
            params = dict(self._config.get("params", {}))
            params[delta_param] = since
            resp = self._get_session().get(resource, params=params)
            resp.raise_for_status()
            return self._extract_records(resp.json())
        except Exception as e:
            logger.warning(f"REST pull_delta 失败: {e}")
            return []

    def _extract_records(self, data: Any) -> list[dict]:
        """从 API 响应中提取记录列表"""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            data_path = self._config.get("pagination", {}).get("data_path", "")
            for key in [data_path, "data", "results", "items", "records"]:
                if key and key in data and isinstance(data[key], list):
                    return data[key]
        return []
