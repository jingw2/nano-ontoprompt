"""Celery application (I-BACKEND: guard-before-third-party + Agent tasks).

Worker and beat both load this module; it refuses unsupported Python before
the Celery or pydantic (via `app.config`) imports.  Agent dispatch/index/
turn/retention tasks are registered here so every core worker role loads them.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from check_python_version import require_supported_python

require_supported_python()

from celery import Celery
from app.config import settings

celery_app = Celery("ontexus",
                    broker=settings.redis_url,
                    backend=settings.redis_url,
                    include=[
                        "app.tasks.extraction",
                        "app.tasks.audit",
                        "app.tasks.agent_dispatch",
                        "app.tasks.agent_index",
                        "app.tasks.agent_turn",
                        "app.tasks.agent_retention",
                        "app.tasks.agent_memory",
                        "app.tasks.agent_memory_extraction",
                        "app.tasks.agent_memory_vector",
                        # v2 pipeline / mapping / connection sync tasks
                        "app.tasks.v2.pipeline_run",
                        "app.tasks.v2.mapping_apply",
                        "app.tasks.v2.connection_sync",
                    ])

# 注册全部 ORM 模型 — worker 子进程只加载任务模块, 若 v2 Connection 等模型未
# 导入, 持有外键的 Dataset flush 会报 NoReferencedTableError (找不到 v2_connections)。
from app.models import load_all_models  # noqa: E402

load_all_models()

# broker 不可用时快速失败 (默认会长时间重试, 导致 API 请求阻塞)
celery_app.conf.task_publish_retry = False
celery_app.conf.broker_connection_timeout = 3

# beat: 定期把 pending 的 Agent Turn dispatch outbox 投递到 broker, 由
# agent.turn_execute 的 claim CAS 消费 (无 broker 时行保持 pending 重试)
celery_app.conf.beat_schedule = {
    "agent-dispatch-publish": {
        "task": "agent.dispatch_publish",
        "schedule": 2.0,
    },
    "agent-memory-summary-sweep": {
        "task": "agent.memory_summary_sweep",
        "schedule": 60.0,
    },
    "agent-memory-extraction-sweep": {
        "task": "agent.memory_extraction_sweep",
        "schedule": 60.0,
    },
    "agent-memory-vector-sweep": {
        "task": "agent.memory_vector_sweep",
        "schedule": 60.0,
    },
}
