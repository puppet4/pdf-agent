from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "PDF_AGENT_"}

    # --- App ---
    app_name: str = "PDF Agent Toolbox"
    debug: bool = False

    # --- Database ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pdf_agent"

    # --- Redis / Celery ---
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # --- Storage ---
    data_dir: Path = Path("data")

    # --- Limits ---
    max_upload_size_mb: int = 200
    max_page_count: int = 2000
    external_cmd_timeout_sec: int = 1800  # 30 min

    # --- Cleanup ---
    job_retention_days: int = 7

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
