from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "PDF_AGENT_"}

    # --- App ---
    app_name: str = "PDF Agent Toolbox"
    debug: bool = False

    # --- Database (async for FastAPI) ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pdf_agent"

    # --- LangGraph Checkpointer (sync psycopg) ---
    checkpointer_db_url: str = "postgresql://postgres:postgres@localhost:5432/pdf_agent"

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

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def threads_dir(self) -> Path:
        return self.data_dir / "threads"

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.threads_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
