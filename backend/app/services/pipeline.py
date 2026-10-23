import asyncio
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Document, Job, ModelVersion, ParseRun, SourceBlock
from app.schemas import ModelSnapshot
from app.services.extraction import extract_model
from app.services.parsing import ParserError, parse_document
from app.services.storage import save_document_markdown
from app.services.validation import validate_model


def _set_job(db, job: Job, *, status: str, stage: str, progress: int, message: str | None = None):
    job.status = status
    job.stage = stage
    job.progress = progress
    job.error_message = message
    job.updated_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()


async def run_document_pipeline(job_id: str) -> None:
    db = SessionLocal()
    job = db.get(Job, job_id)
    if job is None:
        db.close()
        return
    document = db.get(Document, job.document_id)
    if document is None:
        _set_job(db, job, status="failed", stage="failed", progress=100, message="文档不存在")
        db.close()
        return

    try:
        document.parse_status = "parsing"
        db.add(document)
        db.commit()
        _set_job(db, job, status="running", stage="parsing", progress=10)
        result = parse_document(document.storage_key, document.extension)
        if result.markdown_content:
            save_document_markdown(document.id, document.filename, result.markdown_content)

        parse_run = ParseRun(
            document_id=document.id,
            parser_name=result.parser_name,
            parser_version=result.parser_version,
            status="completed",
            warnings=result.warnings,
        )
        db.add(parse_run)
        db.flush()
        source_blocks = []
        for index, block in enumerate(result.blocks, start=1):
            source_blocks.append(
                SourceBlock(
                    document_id=document.id,
                    parse_run_id=parse_run.id,
                    block_key=f"{parse_run.id[:8]}-src-{index:04d}",
                    block_type=block.block_type,
                    text=block.text,
                    locator=block.locator,
                    confidence=block.confidence,
                )
            )
        db.add_all(source_blocks)
        db.commit()
        _set_job(db, job, status="running", stage="extracting", progress=45)

        blocks = [{"id": item.block_key, "text": item.text, "locator": item.locator} for item in source_blocks]
        snapshot, extraction_source = await extract_model(blocks)
        source_ids = {item.block_key for item in source_blocks}
        validation = validate_model(snapshot, source_ids)
        snapshot = snapshot.model_copy(
            update={"validation": validation, "buildability_status": validation["buildability_status"]}
        )

        _set_job(db, job, status="running", stage="saving_model", progress=85)
        current = db.scalar(select(func.max(ModelVersion.version_no)).where(ModelVersion.document_id == document.id)) or 0
        model_version = ModelVersion(
            project_id=document.project_id,
            document_id=document.id,
            parse_run_id=parse_run.id,
            version_no=current + 1,
            state="draft",
            model_json={**snapshot.model_dump(), "extraction_source": extraction_source, "parse_warnings": result.warnings},
        )
        db.add(model_version)
        document.parse_status = "completed"
        db.add(document)
        db.commit()
        _set_job(db, job, status="completed", stage="completed", progress=100)
    except (ParserError, ValueError) as exc:
        db.rollback()
        document.parse_status = "failed"
        db.add(document)
        job.status = "failed"
        job.stage = "failed"
        job.progress = 100
        job.error_code = "pipeline_error"
        job.error_message = str(exc)
        db.add(job)
        db.commit()
    except Exception as exc:  # Keep the public job error safe while retaining a server traceback.
        db.rollback()
        document.parse_status = "failed"
        db.add(document)
        job.status = "failed"
        job.stage = "failed"
        job.progress = 100
        job.error_code = "internal_error"
        job.error_message = "处理任务失败，请查看服务日志或重试。"
        db.add(job)
        db.commit()
        print(f"pipeline job {job_id} failed: {exc}")
    finally:
        db.close()


def run_document_pipeline_sync(job_id: str) -> None:
    asyncio.run(run_document_pipeline(job_id))
