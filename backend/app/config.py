from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Simulink 文档驱动建模助手"
    database_url: str = "sqlite:///./storage/app.db"
    storage_dir: Path = Path("./storage")
    max_upload_mb: int = 30
    task_backend: str = "inline"
    redis_url: str = "redis://127.0.0.1:6379/0"
    mineru_command: str = "mineru-kit"
    mineru_timeout_seconds: int = 600
    mineru_pdf_tier: str = "basic"
    mineru_required: bool = False
    llm_enabled: bool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 90
    allowed_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
