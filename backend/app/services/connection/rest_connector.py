"""REST API Connector — 支持分页与增量(since 参数)"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from app.services.connection.base import ConnectorBase
from app.services.connection.base import DeltaPage, records_to_delta_page
from app.schemas.refresh import SourceCursor

logger = logging.getLogger(__name__)
_LEGACY_UNSET = object()


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

    def pull_delta(
        self,
        resource: str,
        *,
        cursor: SourceCursor | None = None,
        overlap_window: timedelta = timedelta(0),
        since: str | None | object = _LEGACY_UNSET,
    ) -> DeltaPage | list[dict]:
        """Incremental query using the configured API cursor parameter."""
        # Compatibility with the pre-Task-8 API.  This branch intentionally
        # keeps the old list return shape for callers that explicitly pass
        # ``since``.
        if since is not _LEGACY_UNSET:
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

        contract = self._config.get("cursor_contract", "opaque_source_cursor")
        delta_param = self._config.get("cursor_param") or self._config.get("delta_param")
        watermark_field = self._config.get("watermark_column", "updated_at")
        primary_key_field = self._config.get("primary_key_column", "id")
        cursor_field = self._config.get("cursor_field", "cursor")
        if contract not in ("watermark_primary_key", "opaque_source_cursor") or not delta_param:
            rows = self.pull_full(resource)
            return records_to_delta_page(
                rows, source_id=self._config.get("source_id", ""), resource=resource,
                contract=contract, cursor=cursor, primary_key_field=primary_key_field,
                watermark_field=watermark_field, cursor_field=cursor_field,
                cursor_outcome="unchanged",
            )

        params = dict(self._config.get("params", {}))
        if cursor is not None:
            value = cursor.opaque_value if contract == "opaque_source_cursor" else cursor.watermark
            if value is not None:
                if contract == "watermark_primary_key" and overlap_window > timedelta(0):
                    value = _subtract_overlap(value, overlap_window)
                params[delta_param] = value
        try:
            resp = self._get_session().get(resource, params=params)
            resp.raise_for_status()
            data = resp.json()
            rows = self._extract_records(data)
            next_cursor = None
            observed_at = None
            if isinstance(data, dict):
                next_cursor = data.get("next_cursor", data.get("nextCursor"))
                if next_cursor is None and isinstance(data.get("meta"), dict):
                    next_cursor = data["meta"].get("next_cursor")
                observed_at = _as_datetime(data.get("source_observed_at") or data.get("observed_at"))
            return records_to_delta_page(
                rows, source_id=self._config.get("source_id", ""), resource=resource,
                contract=contract, cursor=cursor, primary_key_field=primary_key_field,
                watermark_field=watermark_field, cursor_field=cursor_field,
                observed_at=observed_at, next_cursor=next_cursor,
            )
        except Exception as e:
            logger.warning(f"REST pull_delta 失败: {e}")
            raise

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


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _subtract_overlap(value: object, overlap_window: timedelta) -> object:
    if isinstance(value, datetime):
        return value - overlap_window
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            return (parsed - overlap_window).isoformat().replace("+00:00", "Z")
        except ValueError:
            return value
    return value
