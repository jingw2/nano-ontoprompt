"""
Celery 任务 — Connection 同步 (Milestone 1.4 stub)
实际实现将在 Milestone 1.5 之后补充。
"""
from __future__ import annotations


def sync_connection(connection_id: str, mode: str = "full") -> dict:
    """
    同步 Connection 数据。

    Args:
        connection_id: 要同步的 Connection ID
        mode: "full" | "delta"

    Returns:
        {"status": "ok", "rows": int}
    """
    pass


def sync_all_connections() -> list[dict]:
    """
    顺序同步所有处于激活状态的 Connection。

    Returns:
        各 Connection 的同步结果列表
    """
    pass
