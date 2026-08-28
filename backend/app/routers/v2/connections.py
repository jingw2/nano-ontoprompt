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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.database import SessionLocal
from app.deps import get_current_user, require_editor
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
def create_connection(body: ConnectionCreate, db: Session = Depends(get_db), _=Depends(require_editor)):
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


def _build_db_config(raw_config: dict, db_type: str) -> dict:
    """
    ConnectorInspector 发送单个字段（host/port/user/password/database）而非
    connection_string，这里组装成 SQLAlchemy 可用的连接 URL。
    密码中的特殊字符通过 urllib.parse.quote 编码以避免 URL 解析歧义。
    """
    from urllib.parse import quote
    host = raw_config.get("host", "localhost")
    port = raw_config.get("port", "3306" if db_type == "mysql" else "5432")
    user = raw_config.get("user", "")
    password = raw_config.get("password", "")
    database = raw_config.get("database", "")
    # 密码/用户名/库名中的特殊字符（如 @ : / # 空格等）必须 URL 编码，
    # 否则 SQLAlchemy 的 URL 解析器会将 @ 等视为 URL 结构分隔符而非密码的一部分。
    # host 不编码（IPv6 地址用 [] 括起，需原样保留）。
    scheme = "mysql+pymysql" if db_type == "mysql" else "postgresql"
    conn_str = f"{scheme}://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{quote(database, safe='')}"
    config = dict(raw_config)
    config["connection_string"] = conn_str
    return config


@router.post("/test-config")
def test_connection_config(body: TestConfigBody, _=Depends(require_editor)):
    """测试连接配置（无需先创建 Connection，供 Builder 使用）"""
    try:
        cfg = body.config
        if body.type in ("mysql", "postgresql", "postgres"):
            db_type = "postgres" if "postgres" in body.type else "mysql"
            cfg = _build_db_config(cfg, db_type)
        connector = get_connector(body.type, cfg)
        ok = connector.test_connection()
        return {"success": ok}
    except Exception as e:
        return {"success": False, "detail": str(e)}


@router.post("/{connection_id}/test")
def test_connection(connection_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
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

    # 与 test-config 一样，ConnectionsTab 存储的是独立字段而非 connection_string
    if conn.kind in ("mysql", "postgres"):
        config = _build_db_config(config, conn.kind)

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


class RefreshConfigurationUpdate(BaseModel):
    resource: str
    cursor_contract: str  # "watermark_primary_key" | "opaque_source_cursor"
    configuration: dict = {}


@router.put("/{connection_id}/refresh-configuration", response_model=ConnectionResponse)
def update_connection_refresh_configuration(
    connection_id: str, body: RefreshConfigurationUpdate,
    db: Session = Depends(get_db), _=Depends(require_editor),
):
    """管理侧的资源刷新配置修订：在同一数据库事务内更新 Connection 的
    cursor_contract 与该 (connection, resource) 的权威 RefreshSourceState
    修订号，二者必须原子生效——绝不允许 Connection 侧的 JSON/cursor_contract
    单独变更而不递增 config_version、失效旧的 lease/fence。
    """
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    from app.services.v2.incremental.contract import update_source_configuration

    conn.cursor_contract = body.cursor_contract
    # update_source_configuration is the sole commit boundary for this
    # request, so the Connection mutation above lands in the exact same
    # transaction as the RefreshSourceState revision bump below.
    update_source_configuration(
        db, source_id=connection_id, resource=body.resource,
        cursor_contract=body.cursor_contract, configuration=body.configuration,
        now=datetime.now(timezone.utc),
    )
    db.refresh(conn)
    return conn


@router.delete("/{connection_id}", status_code=204)
def delete_connection(connection_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    db.delete(conn)
    db.commit()


class ScheduleUpdate(BaseModel):
    """Enterprise timing controls for a connection's persisted refresh
    schedule — mirrors `schedule_service.ScheduleRequest`'s optional fields
    so `cron_expr` alone keeps behaving exactly as before."""
    cron_expr: str
    timezone: str = "UTC"
    business_calendar: list[str] = []
    sla_seconds: int = 0
    retry_policy: Optional[dict] = None
    backfill_window_seconds: int = 0
    max_pending_runs: int = 1
    enabled: bool = True


@router.post("/{connection_id}/schedule")
def set_schedule(connection_id: str, body: ScheduleUpdate, db: Session = Depends(get_db), _=Depends(require_editor)):
    """Compatibility delegate for the typed refresh schedule operation."""
    from app.services.v2.incremental.operations import RefreshError, update_refresh_schedule
    from app.services.v2.scheduler.cron_service import CronService

    try:
        schedule = update_refresh_schedule(
            db, source_id=connection_id, cron_expr=body.cron_expr,
            timezone_name=body.timezone, business_calendar=body.business_calendar,
            sla_seconds=body.sla_seconds, retry_policy=body.retry_policy,
            backfill_window_seconds=body.backfill_window_seconds,
            max_pending_runs=body.max_pending_runs, enabled=body.enabled,
        )
    except RefreshError as exc:
        if exc.reason_code == "SOURCE_NOT_FOUND":
            raise HTTPException(404, "Connection not found") from exc
        raise HTTPException(422, detail=exc.reason_code) from exc
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return {
        "connection_id": connection_id,
        "cron": body.cron_expr,
        "celery_crontab": CronService().parse_cron(body.cron_expr),
        "status": "scheduled",
        "next_due_at": schedule.next_due_at.isoformat() if schedule.next_due_at else None,
    }


@router.post("/{connection_id}/sync")
def trigger_sync(connection_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    """手动触发数据同步 through the persisted refresh policy.

    The compatibility response/task name is retained for valid batch and
    micro-batch connections, but event-driven sources must use the signed
    webhook route and can never be sent to the polling queue.
    """
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(404, "Connection not found")

    from app.services.v2.incremental.operations import RefreshError
    from app.tasks.v2.refresh_tasks import create_manual_connection_run, refresh_connection_task
    try:
        run = create_manual_connection_run(db, connection_id)
    except RefreshError as exc:
        raise HTTPException(status_code=422, detail=exc.reason_code) from exc

    conn.status = "active"
    db.commit()

    try:
        refresh_connection_task.delay(run.id)
    except Exception as e:
        return {"connection_id": connection_id, "run_id": run.id, "status": "sync_failed",
                "error": f"任务派发失败 (Celery/Redis 不可用?): {e}"}

    return {"connection_id": connection_id, "run_id": run.id, "status": "sync_triggered"}
