from celery import Celery
from app.config import settings

celery_app = Celery("ontoprompt", broker=settings.redis_url, backend=settings.redis_url)

# Ensure both task modules register on this worker
import app.tasks.extraction  # noqa: E402, F401
import app.tasks.audit  # noqa: E402, F401
