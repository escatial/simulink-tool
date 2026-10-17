import hashlib
import re
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.config import get_settings


ALLOWED_EXTENSIONS = {".pdf", ".docx", ".md", ".markdown", ".txt"}
SAFE_FILENAME = re.compile(r"[^a-zA-Z0-9._-]+")


class FileValidationError(ValueError):
    pass


def storage_root() -> Path:
    root = get_settings().storage_dir
    root.mkdir(parents=True, exist_ok=True)
    (root / "uploads").mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    return root


def safe_filename(filename: str) -> str:
    cleaned = SAFE_FILENAME.sub("_", Path(filename or "document").name).strip("._")
    return cleaned[:180] or "document"


async def save_upload(upload: UploadFile, project_id: str) -> tuple[str, int, str, str]:
    filename = safe_filename(upload.filename or "document")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise FileValidationError(f"不支持的文件格式：{extension or 'unknown'}")

    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    target_dir = storage_root() / "uploads" / project_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid4()}_{filename}"
    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                target.unlink(missing_ok=True)
                raise FileValidationError(f"文件不能超过 {get_settings().max_upload_mb} MB")
            digest.update(chunk)
            output.write(chunk)

    return str(target), size, digest.hexdigest(), extension


def save_artifact(model_id: str, filename: str, content: bytes) -> tuple[str, str]:
    target_dir = storage_root() / "artifacts" / model_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid4()}_{safe_filename(filename)}"
    target.write_bytes(content)
    return str(target), hashlib.sha256(content).hexdigest()
