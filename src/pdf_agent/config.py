from __future__ import annotations

from dataclasses import dataclass
import secrets
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings

_UNSET_API_KEY = "__PDF_AGENT_API_KEY_UNSET__"
_EPHEMERAL_NON_PROD_API_KEY = secrets.token_urlsafe(48)
_WEAK_API_KEYS = {
    "",
    _UNSET_API_KEY,
    "changeme",
    "change-me",
    "change-me-in-production",
    "dev-local-api-key",
    "test-api-key",
    "password",
    "123456",
}


@dataclass(frozen=True)
class AuthPolicy:
    enabled: bool
    mode: str
    api_key: str | None
    reason: str


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "PDF_AGENT_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    # --- App ---
    app_name: str = "PDF Agent"
    debug: bool = False
    expose_api_docs: bool = False
    environment: Literal["development", "test", "production"] = "development"

    # --- Database (async for FastAPI) ---
    database_url: str = "postgresql+asyncpg://localhost:5432/pdf_agent"

    # --- OpenAI / LLM ---
    openai_api_key: str = ""
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o"
    agent_temperature: float = 0
    agent_max_iterations: int = 20
    disable_agent_persistence: bool = False

    # --- Storage ---
    data_dir: Path = Path("data")

    # --- Limits ---
    max_upload_size_mb: int = 200
    max_page_count: int = 2000
    external_cmd_timeout_sec: int = 1800  # 30 min
    libreoffice_timeout_sec: int = 120

    # --- Access Control ---
    auth_mode: Literal["required", "optional", "disabled"] = "required"
    api_key: str = _UNSET_API_KEY
    api_key_header_name: str = "X-API-Key"
    exempt_auth_paths: str = "/healthz"
    min_api_key_length: int = 24

    # --- CORS ---
    cors_origins: str = "*"  # comma-separated allowed origins

    # --- LangSmith ---
    langsmith_api_key: str = ""
    langsmith_project: str = "pdf-agent"

    # --- Rate Limiting ---
    rate_limit_rpm: int = 200  # max chat requests per minute per IP, 0 = disabled

    # --- Cleanup ---
    conversation_ttl_hours: int = 72  # delete expired conversation workdirs older than this
    max_storage_gb: int = 10

    # --- Observability ---
    sentry_dsn: str = ""  # if set, enable Sentry error tracking
    metrics_enabled: bool = True
    degrade_on_state_backend_failure: bool = True

    # --- i18n ---
    default_locale: str = "en"  # "en" or "zh"

    # --- Compatibility ---
    legacy_api_compatibility_mode: Literal["disabled", "bridge"] = "bridge"
    legacy_api_phase: Literal["deprecation", "warning", "sunset"] = "deprecation"
    legacy_api_sunset_date: str = "2026-12-31"
    legacy_api_migration_url: str = "/docs/migrations/legacy-api"

    # --- Idempotency ---
    idempotency_ttl_hours: int = 24
    idempotency_processing_timeout_sec: int = 900
    idempotency_max_key_length: int = 128

    # --- Caching ---
    storage_scan_cache_ttl_sec: int = 30
    conversation_stats_cache_ttl_sec: int = 30

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def conversations_dir(self) -> Path:
        return self.data_dir / "conversations"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cors_allow_credentials(self) -> bool:
        return self.cors_origin_list != ["*"]

    @property
    def auth_exempt_path_set(self) -> set[str]:
        return {item.strip() for item in self.exempt_auth_paths.split(",") if item.strip()}

    @property
    def auth_policy(self) -> AuthPolicy:
        mode = self.auth_mode
        env = self.environment
        raw_key = (self.api_key or "").strip()
        key = "" if raw_key == _UNSET_API_KEY else raw_key

        if mode == "disabled":
            if env == "production":
                raise ValueError("PDF_AGENT_AUTH_MODE=disabled is not allowed in production")
            return AuthPolicy(enabled=False, mode=mode, api_key=None, reason="auth disabled by config")

        if mode == "optional":
            if env == "production":
                raise ValueError("PDF_AGENT_AUTH_MODE=optional is not allowed in production")
            if key:
                if key.lower() in _WEAK_API_KEYS:
                    raise ValueError("API key is weak/default; set a strong PDF_AGENT_API_KEY")
                if len(key) < self.min_api_key_length:
                    raise ValueError(
                        f"API key is too short; requires at least {self.min_api_key_length} characters"
                    )
                return AuthPolicy(enabled=True, mode=mode, api_key=key, reason="optional mode with configured key")
            return AuthPolicy(enabled=False, mode=mode, api_key=None, reason="optional mode without API key")

        if not key:
            if env == "production":
                raise ValueError("Production requires PDF_AGENT_API_KEY when auth_mode=required")
            if env in {"development", "test"}:
                return AuthPolicy(
                    enabled=True,
                    mode=mode,
                    api_key=_EPHEMERAL_NON_PROD_API_KEY,
                    reason="required mode with ephemeral non-production API key",
                )
            raise ValueError("PDF_AGENT_API_KEY must be non-empty when auth_mode=required")
        if key.lower() in _WEAK_API_KEYS:
            raise ValueError("API key is weak/default; set a strong PDF_AGENT_API_KEY")
        if len(key) < self.min_api_key_length:
            raise ValueError(f"API key is too short; requires at least {self.min_api_key_length} characters")
        return AuthPolicy(enabled=True, mode=mode, api_key=key, reason="required mode")

    def validate_runtime(self) -> None:
        _ = self.auth_policy
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("PDF_AGENT_ENVIRONMENT must be one of: development, test, production")
        if self.api_key_header_name.strip() == "":
            raise ValueError("PDF_AGENT_API_KEY_HEADER_NAME must be non-empty")
        if self.min_api_key_length < 16:
            raise ValueError("PDF_AGENT_MIN_API_KEY_LENGTH must be >= 16")
        if self.idempotency_ttl_hours <= 0:
            raise ValueError("PDF_AGENT_IDEMPOTENCY_TTL_HOURS must be > 0")
        if self.idempotency_processing_timeout_sec <= 0:
            raise ValueError("PDF_AGENT_IDEMPOTENCY_PROCESSING_TIMEOUT_SEC must be > 0")
        if self.idempotency_max_key_length < 16:
            raise ValueError("PDF_AGENT_IDEMPOTENCY_MAX_KEY_LENGTH must be >= 16")
        if self.idempotency_processing_timeout_sec > self.idempotency_ttl_hours * 3600:
            raise ValueError(
                "PDF_AGENT_IDEMPOTENCY_PROCESSING_TIMEOUT_SEC must be less than idempotency ttl window"
            )
        if self.storage_scan_cache_ttl_sec < 0:
            raise ValueError("PDF_AGENT_STORAGE_SCAN_CACHE_TTL_SEC must be >= 0")
        if self.conversation_stats_cache_ttl_sec < 0:
            raise ValueError("PDF_AGENT_CONVERSATION_STATS_CACHE_TTL_SEC must be >= 0")
        if self.legacy_api_compatibility_mode == "disabled" and self.legacy_api_phase != "sunset":
            raise ValueError(
                "When PDF_AGENT_LEGACY_API_COMPATIBILITY_MODE=disabled, phase must be sunset"
            )

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.conversations_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()


def validate_settings() -> None:
    settings.validate_runtime()
