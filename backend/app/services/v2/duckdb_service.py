"""DuckDB 嵌入式分析引擎服务"""
from __future__ import annotations
import logging
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

# 用子进程预检 DuckDB 可用性 (import 本身可能导致崩溃)
def _probe_duckdb() -> bool:
    """在独立进程中确认 DuckDB 能否 import"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import duckdb; duckdb.connect(':memory:').close()"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


# 模块加载时只检查一次
_DUCKDB_PROBED: bool | None = None


def _is_duckdb_available() -> bool:
    global _DUCKDB_PROBED
    if _DUCKDB_PROBED is None:
        _DUCKDB_PROBED = _probe_duckdb()
    return _DUCKDB_PROBED


class DuckDBService:
    """使用 DuckDB 的大文件处理服务"""

    def __init__(self, memory_limit: str = "2GB", threads: int = 4):
        self._memory_limit = memory_limit
        self._threads = threads
        self._conn = None
        # 根据探测结果设置可用性 (防崩溃)
        if _is_duckdb_available():
            try:
                import duckdb
                self._conn = duckdb.connect(":memory:")
                self._conn.execute(f"SET memory_limit='{memory_limit}'")
                self._conn.execute(f"SET threads={threads}")
                self._available = True
            except Exception as e:
                logger.warning(f"DuckDB connection failed: {e}")
                self._available = False
                self._conn = None
        else:
            logger.info("DuckDB not available on this system (probe failed)")
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def execute(self, sql: str, params: dict | None = None) -> list[dict]:
        """执行 SQL 并以 dict 列表返回结果"""
        if not self._available:
            raise RuntimeError("DuckDB not available")
        result = self._conn.execute(sql, list(params.values()) if params else [])
        cols = [desc[0] for desc in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]

    def infer_schema(self, data: list[dict]) -> list[dict]:
        """从数据推断 schema (列名 + 类型 + null 比例) — 基于 pandas, 无需 DuckDB"""
        if not data:
            return []
        import pandas as pd
        df = pd.DataFrame(data)
        schema = []
        for col in df.columns:
            null_pct = df[col].isna().sum() / len(df) * 100
            dtype = str(df[col].dtype)
            schema.append({
                "name": col,
                "type": self._map_dtype(dtype),
                "null_pct": round(null_pct, 2),
                "sample": str(df[col].dropna().iloc[0]) if not df[col].dropna().empty else None,
            })
        return schema

    def split_wide_table(self, data: list[dict], split_config: dict[str, list[str]]) -> dict[str, list[dict]]:
        """
        split_config 示例:
          {"clean_orders": ["order_id", "amount"], "clean_customers": ["customer_id", "name"]}
        各表名 → 对应列的数据 (去重) — 纯 Python, 无需 DuckDB
        """
        result = {}
        for table_name, columns in split_config.items():
            existing_cols = [c for c in columns if c in (data[0].keys() if data else [])]
            if not existing_cols:
                result[table_name] = []
                continue
            seen: set[str] = set()
            rows: list[dict] = []
            for row in data:
                sub = {c: row.get(c) for c in existing_cols}
                key = str(sorted(sub.items()))
                if key not in seen:
                    seen.add(key)
                    rows.append(sub)
            result[table_name] = rows
        return result

    def preview(self, data: list[dict], limit: int = 100) -> list[dict]:
        """数据预览 (Python 切片)"""
        return data[:limit]

    @staticmethod
    def _map_dtype(dtype: str) -> str:
        mapping = {
            "int64": "integer", "int32": "integer",
            "float64": "float", "float32": "float",
            "bool": "boolean", "object": "string",
            "datetime64": "datetime",
        }
        for k, v in mapping.items():
            if k in dtype:
                return v
        return "string"
