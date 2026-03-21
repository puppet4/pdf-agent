from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "PDF_AGENT_"}

    # --- App ---
    app_name: str = "PDF Agent"
    debug: bool = False
    expose_api_docs: bool = False

    # --- Database (async for FastAPI) ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pdf_agent"

    # --- OpenAI / LLM ---
    openai_api_key: str = ""
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o"
    agent_temperature: float = 0
    agent_max_iterations: int = 20

    # --- Storage ---
    data_dir: Path = Path("data")

    # --- Limits ---
    max_upload_size_mb: int = 200
    max_page_count: int = 2000
    external_cmd_timeout_sec: int = 1800  # 30 min

    # --- Access Control ---
    api_key: str = ""  # if set, require X-API-Key header for all API calls

    # --- CORS ---
    cors_origins: str = "*"  # comma-separated allowed origins

    # --- LangSmith ---
    langsmith_api_key: str = ""
    langsmith_project: str = "pdf-agent"

    # --- Rate Limiting ---
    rate_limit_rpm: int = 20  # max chat requests per minute per IP, 0 = disabled

    # --- Cleanup ---
    thread_ttl_hours: int = 72  # delete thread workdirs older than this
    max_storage_gb: int = 10

    # --- Observability ---
    sentry_dsn: str = ""  # if set, enable Sentry error tracking
    metrics_enabled: bool = True

    # --- i18n ---
    default_locale: str = "en"  # "en" or "zh"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def threads_dir(self) -> Path:
        return self.data_dir / "threads"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cors_allow_credentials(self) -> bool:
        return self.cors_origin_list != ["*"]

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.threads_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
