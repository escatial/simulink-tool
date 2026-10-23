from pathlib import Path
from datetime import timezone

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base, engine, get_db
from app.models import Artifact, Document, Job, ModelVersion, ParseRun, Project, SourceBlock
from app.schemas import ConfirmRequest, ExportRequest, ModelEdit, ModelSnapshot, ParseRequest, ProjectCreate
from app.services.dispatcher import dispatch_parse_job
from app.services.exporter import build_artifacts, build_zip
from app.services.llm import LLMUnavailable, fetch_upstream_models
from app.services.storage import (
    FileValidationError,
    document_markdown_path,
    get_selected_llm_model,
    save_artifact,
    save_selected_llm_model,
    save_upload,
)
from app.services.validation import validate_model


settings = get_settings()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LLMModelSelection(BaseModel):
    model: str = Field(min_length=1, max_length=200)


def project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def model_or_404(db: Session, model_id: str) -> ModelVersion:
    model = db.get(ModelVersion, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="方案版本不存在")
    return model


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "version": app.version}


@app.get("/api/v1/llm/models")
async def list_llm_models() -> dict:
    try:
        models = await fetch_upstream_models()
    except LLMUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"models": models, "selected_model": get_selected_llm_model(settings.llm_model)}


@app.put("/api/v1/llm/model")
async def select_llm_model(payload: LLMModelSelection) -> dict:
    try:
        models = await fetch_upstream_models()
    except LLMUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if payload.model not in {item["id"] for item in models}:
        raise HTTPException(status_code=400, detail="所选模型不在 MiniMax 上游模型列表中")
    save_selected_llm_model(payload.model)
    return {"selected_model": payload.model}


@app.get("/api/v1/projects")
def list_projects(db: Session = Depends(get_db)) -> list[dict]:
    projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
    return [
        {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "created_at": project.created_at.isoformat(),
            "document_count": db.scalar(select(func.count(Document.id)).where(Document.project_id == project.id)) or 0,
        }
        for project in projects
    ]


@app.post("/api/v1/projects", status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> dict:
    project = Project(name=payload.name.strip(), description=payload.description.strip())
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name, "description": project.description, "created_at": project.created_at.isoformat(), "document_count": 0}


@app.get("/api/v1/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = project_or_404(db, project_id)
    documents = db.scalars(select(Document).where(Document.project_id == project_id).order_by(Document.created_at.desc())).all()
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "created_at": project.created_at.isoformat(),
        "documents": [document_to_dict(document) for document in documents],
    }


@app.post("/api/v1/projects/{project_id}/documents", status_code=202)
async def upload_document(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    project_or_404(db, project_id)
    try:
        storage_key, size, sha256, extension = await save_upload(file, project_id)
    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    document = Document(
        project_id=project_id,
        filename=file.filename or Path(storage_key).name,
        content_type=file.content_type or "application/octet-stream",
        extension=extension,
        size_bytes=size,
        sha256=sha256,
        storage_key=storage_key,
        parse_status="queued",
    )
    db.add(document)
    db.flush()
    job = Job(project_id=project_id, document_id=document.id, job_type="parse_document")
    db.add(job)
    db.commit()
    try:
        dispatch_parse_job(background_tasks, job.id)
    except RuntimeError as exc:
        db.delete(job)
        db.delete(document)
        db.commit()
        Path(storage_key).unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"document": document_to_dict(document), "job": job_to_dict(job)}


@app.post("/api/v1/documents/{document_id}/parse", status_code=202)
def parse_document_again(
    document_id: str,
    payload: ParseRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if document.parse_status == "running" and not payload.force:
        raise HTTPException(status_code=409, detail="文档正在解析")
    job = Job(project_id=document.project_id, document_id=document.id, job_type="parse_document")
    document.parse_status = "queued"
    db.add_all([job, document])
    db.commit()
    try:
        dispatch_parse_job(background_tasks, job.id)
    except RuntimeError as exc:
        db.delete(job)
        db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"job": job_to_dict(job)}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job_to_dict(job)


@app.get("/api/v1/documents/{document_id}/blocks")
def list_source_blocks(document_id: str, db: Session = Depends(get_db)) -> list[dict]:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    latest_parse = db.scalar(
        select(ParseRun.id)
        .where(ParseRun.document_id == document_id, ParseRun.status == "completed")
        .order_by(ParseRun.created_at.desc())
        .limit(1)
    )
    blocks = db.scalars(
        select(SourceBlock)
        .where(SourceBlock.document_id == document_id, SourceBlock.parse_run_id == latest_parse)
        .order_by(SourceBlock.block_key)
    ).all() if latest_parse else []
    return [{"id": block.block_key, "type": block.block_type, "text": block.text, "locator": block.locator, "confidence": block.confidence} for block in blocks]


@app.get("/api/v1/documents/{document_id}/markdown")
def download_document_markdown(document_id: str, db: Session = Depends(get_db)) -> FileResponse:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if document.extension != ".pdf":
        raise HTTPException(status_code=409, detail="只有 PDF 文档提供 PDF 转 Markdown 下载")
    path = document_markdown_path(document.id, document.filename)
    if not path.is_file():
        raise HTTPException(status_code=409, detail="Markdown 尚未生成，请等待文档解析完成后重试")
    return FileResponse(path, filename=f"{Path(document.filename).stem}.md", media_type="text/markdown; charset=utf-8")


@app.get("/api/v1/projects/{project_id}/models")
def list_models(project_id: str, db: Session = Depends(get_db)) -> list[dict]:
    project_or_404(db, project_id)
    models = db.scalars(select(ModelVersion).where(ModelVersion.project_id == project_id).order_by(ModelVersion.created_at.desc())).all()
    return [model_to_dict(model, include_snapshot=False) for model in models]


@app.get("/api/v1/models/{model_id}")
def get_model(model_id: str, db: Session = Depends(get_db)) -> dict:
    return model_to_dict(model_or_404(db, model_id), include_snapshot=True)


@app.patch("/api/v1/models/{model_id}")
def edit_model(model_id: str, payload: ModelEdit, db: Session = Depends(get_db)) -> dict:
    current = model_or_404(db, model_id)
    if current.version_no != payload.expected_version:
        raise HTTPException(status_code=409, detail="方案版本已变化，请刷新后再编辑")
    snapshot = ModelSnapshot.model_validate(payload.model)
    source_ids = {
        block.block_key
        for block in db.scalars(
            select(SourceBlock).where(
                SourceBlock.document_id == current.document_id,
                SourceBlock.parse_run_id == current.parse_run_id,
            )
        ).all()
    }
    validation = validate_model(snapshot, source_ids)
    snapshot = snapshot.model_copy(update={"validation": validation, "buildability_status": validation["buildability_status"]})
    next_version = (db.scalar(select(func.max(ModelVersion.version_no)).where(ModelVersion.document_id == current.document_id)) or 0) + 1
    updated = ModelVersion(
        project_id=current.project_id,
        document_id=current.document_id,
        parse_run_id=current.parse_run_id,
        version_no=next_version,
        state="draft",
        model_json={**snapshot.model_dump(), "revision_of": current.id},
    )
    db.add(updated)
    db.commit()
    db.refresh(updated)
    return model_to_dict(updated, include_snapshot=True)


@app.post("/api/v1/models/{model_id}/confirm")
def confirm_model(model_id: str, payload: ConfirmRequest, db: Session = Depends(get_db)) -> dict:
    current = model_or_404(db, model_id)
    if current.state != "draft":
        raise HTTPException(status_code=409, detail="只有草稿版本可以确认")
    snapshot = ModelSnapshot.model_validate(current.model_json)
    issues = snapshot.validation.get("issues", [])
    if any(issue.get("severity") == "error" for issue in issues) and not payload.acknowledge_issues:
        raise HTTPException(status_code=409, detail="方案仍有阻断问题，补充参数或确认歧义后再提交")
    next_version = (db.scalar(select(func.max(ModelVersion.version_no)).where(ModelVersion.document_id == current.document_id)) or 0) + 1
    confirmed = ModelVersion(
        project_id=current.project_id,
        document_id=current.document_id,
        parse_run_id=current.parse_run_id,
        version_no=next_version,
        state="confirmed",
        model_json={**current.model_json, "issues_acknowledged": payload.acknowledge_issues},
    )
    db.add(confirmed)
    db.commit()
    db.refresh(confirmed)
    return model_to_dict(confirmed, include_snapshot=True)


@app.post("/api/v1/models/{model_id}/exports", status_code=201)
def export_model(model_id: str, payload: ExportRequest, db: Session = Depends(get_db)) -> dict:
    model = model_or_404(db, model_id)
    if model.state != "confirmed":
        raise HTTPException(status_code=409, detail="请先确认方案版本，再导出建模方案包")
    snapshot = ModelSnapshot.model_validate(model.model_json)
    formats = list(dict.fromkeys(payload.formats))
    files = build_artifacts(snapshot, formats)
    artifacts = []
    for filename, content in files.items():
        path, digest = save_artifact(model.id, filename, content)
        artifact = Artifact(
            project_id=model.project_id,
            model_version_id=model.id,
            artifact_type=Path(filename).suffix.lstrip(".") or "file",
            filename=filename,
            storage_key=path,
            sha256=digest,
        )
        db.add(artifact)
        db.flush()
        artifacts.append(artifact_to_dict(artifact))
    zip_content = build_zip(snapshot, formats)
    zip_path, zip_digest = save_artifact(model.id, "建模方案.zip", zip_content)
    zip_artifact = Artifact(
        project_id=model.project_id,
        model_version_id=model.id,
        artifact_type="zip",
        filename="建模方案.zip",
        storage_key=zip_path,
        sha256=zip_digest,
    )
    db.add(zip_artifact)
    db.commit()
    artifacts.append(artifact_to_dict(zip_artifact))
    return {"model_version_id": model.id, "artifacts": artifacts}


@app.get("/api/v1/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, db: Session = Depends(get_db)) -> FileResponse:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None or not Path(artifact.storage_key).is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")
    return FileResponse(artifact.storage_key, filename=artifact.filename, media_type="application/octet-stream")


def document_to_dict(document: Document) -> dict:
    markdown_available = document.extension == ".pdf" and document_markdown_path(document.id, document.filename).is_file()
    return {
        "id": document.id,
        "project_id": document.project_id,
        "filename": document.filename,
        "content_type": document.content_type,
        "extension": document.extension,
        "size_bytes": document.size_bytes,
        "sha256": document.sha256,
        "parse_status": document.parse_status,
        "markdown_available": markdown_available,
        "markdown_download_url": f"/api/v1/documents/{document.id}/markdown" if markdown_available else None,
        "created_at": document.created_at.isoformat(),
    }


def job_to_dict(job: Job) -> dict:
    updated_at = job.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return {
        "id": job.id,
        "project_id": job.project_id,
        "document_id": job.document_id,
        "job_type": job.job_type,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
    }


def model_to_dict(model: ModelVersion, include_snapshot: bool) -> dict:
    payload = {
        "id": model.id,
        "project_id": model.project_id,
        "document_id": model.document_id,
        "parse_run_id": model.parse_run_id,
        "version_no": model.version_no,
        "state": model.state,
        "buildability_status": model.model_json.get("buildability_status", "仅供参考"),
        "system_name": model.model_json.get("system_name", "未命名控制系统"),
        "created_at": model.created_at.isoformat(),
    }
    if include_snapshot:
        payload["model"] = model.model_json
    return payload


def artifact_to_dict(artifact: Artifact) -> dict:
    return {
        "id": artifact.id,
        "type": artifact.artifact_type,
        "filename": artifact.filename,
        "sha256": artifact.sha256,
        "download_url": f"/api/v1/artifacts/{artifact.id}/download",
        "created_at": artifact.created_at.isoformat(),
    }
