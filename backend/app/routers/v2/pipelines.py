"""v2 Pipeline API — 支持新 DSL (nodes/edges) + 旧 steps 格式兼容"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone
from app.database import SessionLocal
from app.deps import get_current_user
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineVersion
# 确保 Dataset 模型先导入以解析 FK
import app.models.v2.dataset  # noqa: F401

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Pydantic Models ────────────────────────────────────────────────

class PipelineCreate(BaseModel):
    name: str
    domain: str = "通用"
    description: str = ""
    source_dataset_id: Optional[str] = None
    route: Optional[str] = None  # A|B|C (legacy)
    spec: Optional[dict] = None  # legacy steps
    definition: Optional[dict] = None  # new DSL: {nodes: [...], edges: [...]}


class PipelineUpdate(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    source_dataset_id: Optional[str] = None
    route: Optional[str] = None
    spec: Optional[dict] = None
    definition: Optional[dict] = None
    status: Optional[str] = None


class PipelineResponse(BaseModel):
    id: str
    name: str
    domain: Optional[str] = "通用"
    description: Optional[str] = ""
    source_dataset_id: Optional[str] = None
    route: Optional[str] = None
    spec: Optional[dict] = None
    definition: Optional[dict] = None
    status: str = "draft"
    branch: Optional[str] = "main"
    version: int = 1
    target_curated_ids: Optional[list] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class ValidateResult(BaseModel):
    valid: bool
    errors: list[dict] = []
    warnings: list[dict] = []


# ── CRUD ──────────────────────────────────────────────────────────

@router.post("", response_model=PipelineResponse, status_code=201)
def create_pipeline(body: PipelineCreate, db: Session = Depends(get_db)):
    """创建新 Pipeline。支持旧 steps 格式和新 nodes/edges DSL。"""
    # 重名校验
    existing = db.query(Pipeline).filter(
        Pipeline.name == body.name,
        Pipeline.domain == body.domain,
    ).first()
    if existing:
        raise HTTPException(400, "已存在同名 Pipeline，请更换名称。")

    # 从 definition 推断 route，如无法推断默认为 'A'
    inferred_route = body.route
    if not inferred_route and body.definition:
        nodes = body.definition.get("nodes", [])
        types = {n.get("type") for n in nodes if n.get("type")}
        if "transform" in types:
            # 根据 Transform 节点配置推断
            pass  # 保留默认
        inferred_route = inferred_route or "A"
    pl = Pipeline(
        name=body.name,
        domain=body.domain or "通用",
        description=body.description or "",
        source_dataset_id=body.source_dataset_id,
        route=inferred_route or "A",  # SQLite 该列有 NOT NULL 约束
        spec=body.spec or {},
        definition=body.definition,
        status="draft",
        branch="main",
        version=1,
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    return _format_pipeline(pl)


@router.get("", response_model=list[dict])
def list_pipelines(
    search: str = "",
    domain: str = "",
    status: str = "",
    db: Session = Depends(get_db),
):
    """Pipeline 列表，支持按名称/ID/域/状态搜索。"""
    q = db.query(Pipeline)
    if search:
        q = q.filter(
            Pipeline.name.ilike(f"%{search}%") | Pipeline.id.ilike(f"%{search}%")
        )
    if domain:
        q = q.filter(Pipeline.domain == domain)
    if status:
        q = q.filter(Pipeline.status == status)
    q = q.order_by(Pipeline.updated_at.desc()).limit(100)
    results = []
    for pl in q:
        d = _format_pipeline(pl)
        # 添加最近运行信息
        last_run = db.query(PipelineRun).filter(
            PipelineRun.pipeline_id == pl.id
        ).order_by(PipelineRun.created_at.desc()).first()
        if last_run:
            d["last_run_status"] = last_run.status
            d["last_run_at"] = (
                last_run.started_at.isoformat() if last_run.started_at else None
            )
        results.append(d)
    return results


@router.get("/{pipeline_id}", response_model=PipelineResponse)
def get_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    return _format_pipeline(pl)


@router.put("/{pipeline_id}", response_model=PipelineResponse)
def update_pipeline(pipeline_id: str, body: PipelineUpdate, db: Session = Depends(get_db)):
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")

    update_data = body.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(pl, k, v)
    pl.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(pl)
    return _format_pipeline(pl)


@router.delete("/{pipeline_id}")
def delete_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")
    # 级联删除 runs + versions
    db.query(PipelineRun).filter(PipelineRun.pipeline_id == pipeline_id).delete()
    db.query(PipelineVersion).filter(PipelineVersion.pipeline_id == pipeline_id).delete()
    db.delete(pl)
    db.commit()
    return {"status": "deleted", "id": pipeline_id}


# ── Validate ──────────────────────────────────────────────────────

@router.post("/{pipeline_id}/validate", response_model=ValidateResult)
def validate_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    """校验 Pipeline definition 是否合法。"""
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")

    errors = []
    warnings = []
    definition = pl.definition

    if not definition:
        # 旧格式：无 definition，认为合法
        return ValidateResult(valid=True, errors=[], warnings=[])

    nodes = definition.get("nodes", [])
    edges = definition.get("edges", [])

    if not nodes:
        errors.append({"node_id": "", "severity": "error", "message": "Pipeline 至少需要一个节点。"})

    node_ids = set()
    node_types = {}
    for n in nodes:
        nid = n.get("id", "")
        node_ids.add(nid)
        node_types[nid] = n.get("type", "")

    # 检查连接规则
    for edge in edges:
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        if src not in node_ids:
            errors.append({"node_id": src, "severity": "error", "message": f"边引用了不存在的源节点: {src}"})
        if tgt not in node_ids:
            errors.append({"node_id": tgt, "severity": "error", "message": f"边引用了不存在的目标节点: {tgt}"})

        src_type = node_types.get(src, "")
        tgt_type = node_types.get(tgt, "")

        # Connector → Transform 不允许
        if src_type == "connector" and tgt_type == "transform":
            errors.append({
                "node_id": edge.get("id", ""),
                "severity": "error",
                "message": "Connector 不能直接连接 Transform，需要经过 Storage。",
            })
        # Connector → Output 不允许
        if src_type == "connector" and tgt_type == "output":
            errors.append({
                "node_id": edge.get("id", ""),
                "severity": "error",
                "message": "Connector 不能直接连接 Output。",
            })
        # Output → anything 不允许
        if src_type == "output":
            errors.append({
                "node_id": edge.get("id", ""),
                "severity": "error",
                "message": "Output 节点不能作为边的起点。",
            })

    # 检查是否有至少一条 Connector → Storage → Transform → Output 路径
    has_connector = any(t == "connector" for t in node_types.values())
    has_output = any(t == "output" for t in node_types.values())
    if has_connector and not has_output:
        warnings.append({
            "node_id": "",
            "severity": "warning",
            "message": "存在 Connector 但没有 Output 节点，Pipeline 不会产生输出。",
        })

    return ValidateResult(valid=len(errors) == 0, errors=errors, warnings=warnings)


# ── Publish ───────────────────────────────────────────────────────

@router.post("/{pipeline_id}/publish")
def publish_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    """发布 Pipeline 为稳定版本。"""
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")

    # 先校验
    validation = validate_pipeline(pipeline_id, db)
    if not validation.valid:
        raise HTTPException(400, f"Pipeline 校验失败，无法发布: {validation.errors}")

    pl.status = "published"
    pl.version = (pl.version or 1) + 1
    pl.updated_at = datetime.now(timezone.utc)
    db.commit()

    # 保存版本快照
    version_record = PipelineVersion(
        pipeline_id=pipeline_id,
        version=pl.version,
        definition=pl.definition,
        status="published",
    )
    db.add(version_record)
    db.commit()

    return {
        "id": pl.id,
        "status": pl.status,
        "version": pl.version,
    }


# ── Versions ──────────────────────────────────────────────────────

@router.get("/{pipeline_id}/versions")
def list_versions(pipeline_id: str, db: Session = Depends(get_db)):
    """查看版本历史。"""
    versions = db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == pipeline_id
    ).order_by(PipelineVersion.version.desc()).all()
    return [
        {
            "id": v.id,
            "version": v.version,
            "status": v.status,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in versions
    ]


# ── Run (保留原有) ────────────────────────────────────────────────

@router.post("/{pipeline_id}/run")
def run_pipeline(pipeline_id: str, db: Session = Depends(get_db)):
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")

    pl.status = "running"
    run = PipelineRun(pipeline_id=pipeline_id, status="pending", started_at=datetime.now(timezone.utc))
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        _ensure_broker_reachable()
        from app.tasks.v2.pipeline_run import pipeline_run_task
        pipeline_run_task.delay(pipeline_id, run.id)
    except Exception as e:
        # Celery/Redis 不可用时立即标记失败，避免 run 永远停在 pending
        run.status = "failed"
        run.error_log = f"任务派发失败 (Celery/Redis 不可用?): {e}"
        run.finished_at = datetime.now(timezone.utc)
        pl.status = "failed"
        db.commit()
        return {"run_id": run.id, "status": "failed", "error": run.error_log}

    return {"run_id": run.id, "status": "pending"}


def _ensure_broker_reachable(timeout: float = 2.0):
    """快速预检 Celery broker 可达性 — kombu 自身的连接重试会阻塞请求数十秒"""
    import socket
    from urllib.parse import urlparse
    from app.config import settings
    u = urlparse(settings.redis_url)
    sock = socket.create_connection((u.hostname or "localhost", u.port or 6379), timeout=timeout)
    sock.close()


@router.get("/{pipeline_id}/runs")
def list_runs(pipeline_id: str, db: Session = Depends(get_db)):
    runs = db.query(PipelineRun).filter(PipelineRun.pipeline_id == pipeline_id).order_by(PipelineRun.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in runs
    ]


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Run not found")
    return {
        "id": run.id,
        "status": run.status,
        "stats": run.stats,
        "error_log": run.error_log,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.post("/{pipeline_id}/run-sync")
def run_pipeline_sync(pipeline_id: str, db: Session = Depends(get_db)):
    """同步执行 Pipeline（无需 Celery/Redis，适用于开发/测试）"""
    pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pl:
        raise HTTPException(404, "Pipeline not found")

    pl.status = "running"
    run = PipelineRun(pipeline_id=pipeline_id, status="pending", started_at=datetime.now(timezone.utc))
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        from app.tasks.v2.pipeline_run import pipeline_run_task
        pipeline_run_task(pipeline_id, run.id)
        db.refresh(run)
        pl.status = "published" if run.status == "success" else "failed"
        db.commit()
        return {"run_id": run.id, "status": run.status, "stats": run.stats, "error": run.error_log}
    except Exception as e:
        pl.status = "failed"
        db.commit()
        return {"run_id": run.id, "status": "failed", "error": str(e)}


class PreviewStepBody(BaseModel):
    op: str
    params: dict = {}
    sample_data: list[dict] = []


@router.post("/preview-step")
def preview_step(body: PreviewStepBody):
    """预览某个 Transform 步骤的输出"""
    try:
        from app.services.v2.pipeline.steps.cleansing import CleansingStep
        from app.services.v2.pipeline.steps.schema_inference import SchemaInferenceStep
        from app.services.v2.pipeline.base import PipelineContext

        ctx = PipelineContext(dataset_id="", version_no=1, route="A", spec={})
        data = body.sample_data or [{"col": "sample"}]

        if body.op in ("drop_duplicates", "fill_nulls", "normalize_dates"):
            step = CleansingStep()
            data = step.run(ctx, data)
        elif body.op == "schema_inference":
            step = SchemaInferenceStep()
            data = step.run(ctx, data)

        return {"op": body.op, "rows_in": len(body.sample_data), "rows_out": len(data), "preview": data[:20]}
    except Exception as e:
        return {"op": body.op, "error": str(e), "rows_in": 0, "rows_out": 0, "preview": []}


# ── Helper ────────────────────────────────────────────────────────

def _format_pipeline(pl: Pipeline) -> dict:
    return {
        "id": pl.id,
        "name": pl.name,
        "domain": pl.domain or "通用",
        "description": pl.description or "",
        "source_dataset_id": pl.source_dataset_id,
        "route": pl.route,
        "spec": pl.spec or {},
        "definition": pl.definition,
        "status": pl.status or "draft",
        "branch": pl.branch or "main",
        "version": pl.version or 1,
        "target_curated_ids": pl.target_curated_ids or [],
        "created_at": pl.created_at.isoformat() if pl.created_at else None,
        "updated_at": pl.updated_at.isoformat() if pl.updated_at else None,
    }
