"""关系型数据库 Connector — MySQL / PostgreSQL"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import create_engine, inspect, text

from app.services.connection.base import ConnectorBase

# 合法 SQL 标识符：字母、数字、下划线、点（schema.table）；禁止空格/分号/引号/注释等
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _validate_identifier(name: str, field: str = "resource") -> str:
    """校验表名/列名等 SQL 标识符，防止 SQL 注入。"""
    if not name or not _IDENT_RE.match(name):
        raise ValueError(f"Invalid {field}: {name!r}")
    return name


class SQLConnector(ConnectorBase):
    """
    基于 SQLAlchemy 的关系型数据库 Connector。
    config 示例:
      {
        "connection_string": "postgresql://user:pass@host:5432/db",
        "query": "SELECT * FROM orders",
        "watermark_column": "updated_at"   # APPEND 模式使用
      }
    """

    def __init__(self, config: dict):
        self._config = config
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            self._engine = create_engine(
                self._config["connection_string"],
                pool_pre_ping=True,
                connect_args={"connect_timeout": 10},
            )
        return self._engine

    def test_connection(self) -> bool:
        try:
            with self._get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def list_resources(self) -> list[str]:
        """返回数据库中的表列表"""
        inspector = inspect(self._get_engine())
        return inspector.get_table_names()

    def pull_sample(self, resource: str, limit: int = 100) -> list[dict]:
        """从表中查询样本数据"""
        _validate_identifier(resource)
        with self._get_engine().connect() as conn:
            result = conn.execute(
                text(f"SELECT * FROM {resource} LIMIT :limit"),
                {"limit": limit},
            )
            cols = list(result.keys())
            return [dict(zip(cols, row)) for row in result]

    def pull_full(self, resource: str) -> list[dict]:
        """查询表全量数据"""
        import pandas as pd
        _validate_identifier(resource)
        query = self._config.get("query") or f"SELECT * FROM {resource}"
        return pd.read_sql(query, self._get_engine()).to_dict(orient="records")

    def pull_delta(self, resource: str, since: str | None = None) -> list[dict]:
        """增量数据查询 (基于 watermark_column)"""
        _validate_identifier(resource)
        watermark_col = self._config.get("watermark_column")
        if not watermark_col or not since:
            return self.pull_full(resource)

        _validate_identifier(watermark_col, field="watermark_column")
        base_query = self._config.get("query") or f"SELECT * FROM {resource}"
        # 包装为子查询后追加 WHERE 子句
        delta_query = f"""
            SELECT * FROM ({base_query}) _t
            WHERE {watermark_col} > :since
        """
        with self._get_engine().connect() as conn:
            result = conn.execute(text(delta_query), {"since": since})
            cols = list(result.keys())
            return [dict(zip(cols, row)) for row in result]
