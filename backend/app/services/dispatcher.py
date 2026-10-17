from fastapi import BackgroundTasks

from app.config import get_settings
from app.services.pipeline import run_document_pipeline_sync


def dispatch_parse_job(background_tasks: BackgroundTasks, job_id: str) -> None:
    if get_settings().task_backend.lower() == "celery":
        try:
            from app.worker import parse_document_task
        except (ImportError, RuntimeError) as exc:
            raise RuntimeError("TASK_BACKEND=celery 需要安装 requirements-worker.txt") from exc
        parse_document_task.delay(job_id)
        return
    background_tasks.add_task(run_document_pipeline_sync, job_id)
