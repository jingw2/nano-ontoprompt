"""
Connection 同步 — 兼容包装器 (Task 7)

历史上这里是独立的同步 stub；现在委托给 app.tasks.v2.refresh_tasks 的
run-ID-only 刷新任务 (refresh.connection / refresh.dispatch_due_schedules
所在的持久化刷新契约)，不再直接执行同步拉取。
"""
from __future__ import annotations


def sync_connection(connection_id: str, mode: str = "full") -> dict:
    """
    同步 Connection 数据（兼容包装器）。

    Args:
        connection_id: 要同步的 Connection ID
        mode: 保留用于向后兼容调用方；当前刷新契约（Task 6/7）不区分
            full/delta 模式 —— 增量范围由 RefreshSourceState 的游标契约决定。

    Returns:
        app.tasks.v2.refresh_tasks.trigger_connection_refresh 的派发结果
    """
    from app.tasks.v2.refresh_tasks import trigger_connection_refresh
    return trigger_connection_refresh(connection_id)


def sync_all_connections() -> list[dict]:
    """
    为所有处于激活状态的 Connection 各触发一次刷新（兼容包装器）。

    Returns:
        各 Connection 的派发结果列表
    """
    from app.database import SessionLocal
    from app.models.v2.connection import Connection
    from app.tasks.v2.refresh_tasks import trigger_connection_refresh

    db = SessionLocal()
    try:
        connections = db.query(Connection).filter(Connection.status == "active").all()
        connection_ids = [conn.id for conn in connections]
    finally:
        db.close()

    return [trigger_connection_refresh(connection_id) for connection_id in connection_ids]
