"""
v2 Connection 管理 API
POST   /api/v2/connections
GET    /api/v2/connections
GET    /api/v2/connections/{id}
POST   /api/v2/connections/{id}/test
DELETE /api/v2/connections/{id}
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.database import SessionLocal
from app.deps import get_current_user
from app.models.v2.connection import Connection
from app.services.connection.registry import get_connector

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Pydantic 模式 ─────────────────────────────────────────────

class ConnectionCreate(BaseModel):
    name: str
    kind: str  # file | mysql | postgres | mongo | rest
    config: dict  # 明文连接配置 (服务端加密)


class ConnectionResponse(BaseModel):
    id: str
    name: str
    kind: str
    status: str

    class Config:
        from_attributes = True


# ── 端点 ──────────────────────────────────────────────────────

@router.post("", response_model=ConnectionResponse, status_code=201)
def create_connection(body: ConnectionCreate, db: Session = Depends(get_db)):
    """创建连接。config 加密后存储。"""
    from app.services import encryption_service
    encrypted_config = {"_encrypted": encryption_service.encrypt(json.dumps(body.config))}

    conn = Connection(
        name=body.name,
        kind=body.kind,
        config=encrypted_config,
        status="inactive",
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


@router.get("", response_model=list[ConnectionResponse])
def list_connections(db: Session = Depends(get_db)):
    return db.query(Connection).all()


@router.get("/{connection_id}", response_model=ConnectionResponse)
def get_connection(connection_id: str, db: Session = Depends(get_db)):
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


class TestConfigBody(BaseModel):
    type: str
    config: dict = {}


@router.post("/test-config")
def test_connection_config(body: TestConfigBody):
    """测试连接配置（无需先创建 Connection，供 Builder 使用）"""
    try:
        connector = get_connector(body.type, body.config)
        ok = connector.test_connection()
        return {"success": ok}
    except Exception as e:
        return {"success": False, "detail": str(e)}


@router.post("/{connection_id}/test")
def test_connection(connection_id: str, db: Session = Depends(get_db)):
    """连接测试。尝试真实连接并返回结果。"""
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    from app.services import encryption_service
    raw = conn.config.get("_encrypted", "")
    try:
        config = json.loads(encryption_service.decrypt(raw)) if raw else conn.config
    except Exception:
        config = conn.config

    try:
        connector = get_connector(conn.kind, config)
        ok = connector.test_connection()
        conn.status = "active" if ok else "error"
        db.commit()
        return {"success": ok, "status": conn.status}
    except Exception as e:
        conn.status = "error"
        db.commit()
        return {"success": False, "status": "error", "detail": str(e)}


@router.delete("/{connection_id}", status_code=204)
def delete_connection(connection_id: str, db: Session = Depends(get_db)):
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    db.delete(conn)
    db.commit()


@router.post("/{connection_id}/schedule")
def set_schedule(connection_id: str, cron_expr: str, db: Session = Depends(get_db)):
    """为连接设置 Cron 调度表达式"""
    from app.services.v2.scheduler.cron_service import CronService
    svc = CronService()
    if not svc.validate_cron(cron_expr):
        raise HTTPException(400, f"无效的 cron 表达式: {cron_expr}")

    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(404, "Connection not found")

    result = svc.schedule_connection_sync(connection_id, cron_expr)
    config = conn.config or {}
    config["schedule_cron"] = cron_expr
    conn.config = config
    db.commit()
    return result


@router.post("/{connection_id}/sync")
def trigger_sync(connection_id: str, db: Session = Depends(get_db)):
    """手动触发数据同步"""
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(404, "Connection not found")

    conn.status = "active"
    db.commit()

    try:
        from app.tasks.v2.sync_tasks import connection_sync_task
        connection_sync_task.delay(connection_id)
    except Exception as e:
        return {"connection_id": connection_id, "status": "sync_failed",
                "error": f"任务派发失败 (Celery/Redis 不可用?): {e}"}

    return {"connection_id": connection_id, "status": "sync_triggered"}
