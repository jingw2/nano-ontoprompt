"""
OntoPrompt API v2

架构：FastAPI + PostgreSQL + Neo4j + ChromaDB + MinIO + Celery/Redis
v2 新增：Pipelines 全链路（Connection→Dataset→Transform→Curated→Mapping）
v1 兼容：/api/v1/* 路由全部保留

启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
# I-BACKEND: refuse unsupported Python before any third-party import
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_python_version import require_supported_python

require_supported_python()

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session
import logging
from app.database import SessionLocal
from app.config import settings

logger = logging.getLogger(__name__)
from app.routers import auth, users, overview, ontologies, files, prompts, models, entities, logic, actions, extraction, graph, settings as settings_router, export, audit
from app.routers.models import admin_router as models_admin_router
from app.routers.ontology_data_grants import router as data_grants_router
from app.routers.application_state_schemas import router as app_state_schemas_router
from app.routers import retention
from app.routers import tool_connections
from app.routers import skills
from app.routers import (
    agents as agents_module,
    agent_approvals,
    agent_application_state,
    agent_clarifications,
    agent_events,
    agent_reconciliations,
    agent_turns,
    ontology_access_grants,
    ontology_lifecycle,
    ontology_remediations,
    security_domains,
)
from app.routers import oauth as oauth_router
from app.routers import mcp_write_requests as mcp_write_requests_router
from app.routers.v2 import connections as connections_v2
from app.routers.v2 import datasets as datasets_v2
from app.routers.v2 import pipelines as pipelines_v2
from app.routers.v2 import graph as graph_v2
from app.routers.v2 import search as search_v2
from app.routers.v2 import curated as curated_v2
from app.routers.v2 import mappings as mappings_v2
from app.routers.v2 import incremental as incremental_v2
from app.routers.v2 import logic_actions as logic_actions_v2

def _seed_db():
    from app.services.auth_service import seed_admin
    from app.models.rules_config import RulesConfig
    import uuid

    db = SessionLocal()
    try:
        # Import all models to ensure tables are created
        from app.models import user, ontology, file, prompt, model_config, entity, logic as logic_model, action, relation, extraction_task, rules_config, audit_task
        from app.models import user, ontology, file, prompt, model_config, entity, logic as logic_model, action, relation, extraction_task, rules_config
        from app.models.v2 import dataset as v2_dataset, pipeline as v2_pipeline, connection as v2_connection  # noqa: F401
        from app.models.v2.logic import OntologyLogicRule, OntologyStateMachine  # noqa: F401
        from app.models.v2.action import OntologyActionType, OntologyActionRun  # noqa: F401
        from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit  # noqa: F401
        from app.models.v2.mapping import OntologyMapping, OntologyLinkMapping  # noqa: F401
        seed_admin(db)

        # 重启时清理遗留的 running 任务 — daemon 线程被杀后 task 会永久卡在 85%
        from app.models.extraction_task import ExtractionTask
        stale = db.query(ExtractionTask).filter(ExtractionTask.status == "running").all()
        for t in stale:
            t.status = "failed"
            t.error  = "服务重启，任务中断。请重新触发提取。"
        if stale:
            db.commit()

        # Seed confidence rules
        if db.query(RulesConfig).count() == 0:
            rules = [
                ("confidence_entity_min", "0.5", "实体最低置信度", "Entity min confidence"),
                ("confidence_logic_min", "0.6", "逻辑规则最低置信度", "Logic rule min confidence"),
                ("confidence_action_min", "0.6", "动作最低置信度", "Action min confidence"),
                ("confidence_relation_min", "0.5", "关系最低置信度", "Relation min confidence"),
                ("confidence_high_threshold", "0.9", "高置信度阈值", "High confidence threshold"),
                ("confidence_medium_threshold", "0.7", "中置信度阈值", "Medium confidence threshold"),
                ("confidence_low_threshold", "0.5", "低置信度阈值", "Low confidence threshold"),
                ("confidence_display_dashed_below", "0.7", "低于此值显示虚线边", "Show dashed edge below threshold"),
            ]
            for key, val, label_cn, label_en in rules:
                db.add(RulesConfig(id=str(uuid.uuid4()), rule_key=key, rule_value=val,
                                   rule_label_cn=label_cn, rule_label_en=label_en))
            db.commit()

        # Seed / update builtin prompts (upsert by name)
        from app.models.prompt import Prompt
        from app.models.user import User
        from app.routers.prompts import BUILTIN_PROMPTS
        admin = db.query(User).filter(User.role == "admin").first()
        if admin:
            for p in BUILTIN_PROMPTS:
                existing = db.query(Prompt).filter(Prompt.name == p["name"]).first()
                if existing:
                    existing.content = p["content"]
                    existing.domain = p["domain"]
                else:
                    db.add(Prompt(id=str(uuid.uuid4()), name=p["name"], domain=p["domain"],
                                  content=p["content"], version="v1.0", created_by=admin.id))
            db.commit()
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    _seed_db()
    # 初始化 Neo4j 索引（后台执行，不阻塞启动）
    try:
        from app.services.v2.graph.index_setup import setup_indexes
        setup_indexes()
    except Exception:
        logger.warning("Neo4j index setup skipped (unavailable); startup continues", exc_info=True)
    yield

app = FastAPI(title="OntoPrompt API", version="0.1.0", lifespan=lifespan)

# 注册限流器 - 保护 Auth 等敏感端点
from app.limiter import limiter
from slowapi.middleware import SlowAPIMiddleware
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# I-BACKEND: browser-hardening headers (F0-SECURITY export) + 24h idempotency
# key persistence (Section 12).  The idempotency middleware only inspects
# mutation requests that carry an Idempotency-Key; reads/SSE pass through.
from app.middleware.security_headers import create_security_headers_middleware
from app.middleware.idempotency import create_idempotency_middleware
app.add_middleware(create_security_headers_middleware())
app.add_middleware(create_idempotency_middleware())

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(overview.router, prefix="/api/v1/overview", tags=["overview"])
app.include_router(ontologies.router, prefix="/api/v1/ontologies", tags=["ontologies"])
app.include_router(files.router, prefix="/api/v1/ontologies/{ontology_id}/files", tags=["files"])
app.include_router(entities.router, prefix="/api/v1/ontologies/{ontology_id}/entities", tags=["entities"])
app.include_router(logic.router, prefix="/api/v1/ontologies/{ontology_id}/logic", tags=["logic"])
app.include_router(actions.router, prefix="/api/v1/ontologies/{ontology_id}/actions", tags=["actions"])
app.include_router(extraction.router, prefix="/api/v1/ontologies/{ontology_id}/execute", tags=["extraction"])
app.include_router(graph.router, prefix="/api/v1/ontologies/{ontology_id}/graph", tags=["graph"])
app.include_router(export.router, prefix="/api/v1/ontologies/{ontology_id}/export", tags=["export"])
app.include_router(audit.router, prefix="/api/v1/ontologies/{ontology_id}/audit", tags=["audit"])
app.include_router(prompts.router, prefix="/api/v1/prompts", tags=["prompts"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(models_admin_router)
app.include_router(data_grants_router, prefix="/api/v1/ontology-data-grants", tags=["data-grants"])
app.include_router(app_state_schemas_router, prefix="/api/v2/application-state-schemas", tags=["application-state-schemas"])
app.include_router(agents_module.router, prefix="/api/v1/agents", tags=["agents"])
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["settings"])

# I-BACKEND: core Agent API routers (Section 12 registration)
app.include_router(ontology_lifecycle.router, prefix="/api/v1/ontologies", tags=["ontology-lifecycle"])
app.include_router(ontology_access_grants.router, prefix="/api/v1/ontologies", tags=["ontology-access-grants"])
app.include_router(ontology_access_grants.admin_router, prefix="/api/v1", tags=["ontology-admin"])
app.include_router(ontology_remediations.router, prefix="/api/v1/ontologies", tags=["ontology-remediations"])
app.include_router(security_domains.router, prefix="/api/v1", tags=["security-domains"])
app.include_router(agent_turns.router, prefix="/api/v1", tags=["agent-turns"])
app.include_router(agent_events.router, prefix="/api/v1", tags=["agent-events"])
app.include_router(agent_approvals.router, prefix="/api/v1", tags=["agent-approvals"])
app.include_router(agent_clarifications.router, prefix="/api/v1", tags=["agent-clarifications"])
app.include_router(oauth_router.router, prefix="/api/v1", tags=["oauth"])
# agent_audit is an alias of agent_application_state.router (same object); the
# application-state router carries both the state and audit read routes.
app.include_router(agent_application_state.router, prefix="/api/v1", tags=["agent-application-state"])
app.include_router(agent_reconciliations.router, prefix="/api/v1", tags=["agent-reconciliations"])
app.include_router(mcp_write_requests_router.router, prefix="/api/v1", tags=["mcp-write-requests"])

app.include_router(connections_v2.router, prefix="/api/v2/connections", tags=["v2-connections"])
app.include_router(datasets_v2.router, prefix="/api/v2/datasets", tags=["v2-datasets"])
app.include_router(pipelines_v2.router, prefix="/api/v2/pipelines", tags=["v2-pipelines"])
app.include_router(graph_v2.router, prefix="/api/v2/ontologies", tags=["v2-graph"])
app.include_router(search_v2.router, prefix="/api/v2/ontologies", tags=["v2-search"])
app.include_router(curated_v2.router, prefix="/api/v2/curated", tags=["v2-curated"])
app.include_router(mappings_v2.router, prefix="/api/v2/ontologies", tags=["v2-mappings"])
app.include_router(incremental_v2.router, prefix="/api/v2/incremental", tags=["v2-incremental"])
app.include_router(logic_actions_v2.router, prefix="/api/v2/ontologies", tags=["v2-logic-actions"])
app.include_router(retention.router, prefix="/api/v2", tags=["v2-retention"])
app.include_router(tool_connections.router, prefix="/api/v2", tags=["tool-connections"])
app.include_router(skills.router, prefix="/api/v2", tags=["skills"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health(db: Session = Depends(get_db)):
    checks = {
        "status": "ok",
        "db": "unknown",
        "neo4j": "unknown",
        "minio": "unknown",
        "chroma": "unknown",
    }

    # PostgreSQL check
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"

    # Neo4j check
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        driver.verify_connectivity()
        driver.close()
        checks["neo4j"] = "ok"
    except Exception:
        checks["neo4j"] = "unavailable"

    # MinIO check
    try:
        from minio import Minio
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_use_ssl,
        )
        client.list_buckets()
        checks["minio"] = "ok"
    except Exception:
        checks["minio"] = "unavailable"

    # ChromaDB check
    try:
        import chromadb
        client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
        )
        client.heartbeat()
        checks["chroma"] = "ok"
    except Exception:
        checks["chroma"] = "unavailable"

    return checks
