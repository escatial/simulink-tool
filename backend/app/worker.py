from app.config import get_settings
from app.services.pipeline import run_document_pipeline_sync

try:
    from celery import Celery
except ImportError as exc:
    raise RuntimeError("请先安装 requirements-worker.txt 以启用 Celery Worker") from exc


settings = get_settings()
celery_app = Celery("simulink_tool", broker=settings.redis_url, backend=settings.redis_url)


@celery_app.task(name="simulink_tool.parse_document")
def parse_document_task(job_id: str) -> None:
    run_document_pipeline_sync(job_id)
