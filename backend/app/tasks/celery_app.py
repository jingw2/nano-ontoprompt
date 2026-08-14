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

celery_app = Celery("ontoprompt",
                    broker=settings.redis_url,
                    backend=settings.redis_url,
                    include=[
                        "app.tasks.extraction",
                        "app.tasks.audit",
                        "app.tasks.agent_dispatch",
                        "app.tasks.agent_index",
                        "app.tasks.agent_turn",
                        "app.tasks.agent_retention",
                    ])

# broker 不可用时快速失败 (默认会长时间重试, 导致 API 请求阻塞)
celery_app.conf.task_publish_retry = False
celery_app.conf.broker_connection_timeout = 3
