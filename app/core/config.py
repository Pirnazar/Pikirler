"""Application settings, read from PIKIRLER_* environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ProviderName = Literal["disabled", "gemini", "openai_compatible"]
AsrProviderName = Literal["disabled", "remote_whisper", "openai_compatible", "local_faster_whisper"]

_PLACEHOLDER_SECRETS = {"", "change_me", "changeme"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PIKIRLER_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["development", "test", "production"] = "development"
    secret_key: str = Field(min_length=32)
    api_prefix: str = "/api/v1/pikirler"

    database_url: str
    database_pool_size: int = 10
    database_max_overflow: int = 5
    database_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"

    cors_origins: Annotated[list[str], NoDecode] = []
    default_timezone: str = "UTC"

    # --- auth ---------------------------------------------------------------
    session_cookie_name: str = "pikirler_session"
    csrf_cookie_name: str = "pikirler_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict"] = "lax"
    cookie_domain: str | None = None
    session_ttl_hours: int = 24 * 14
    session_idle_timeout_hours: int = 24 * 3
    magic_word_unlock_minutes: int = 12 * 60
    login_rate_limit_per_ip: int = 10
    login_rate_limit_per_account: int = 5
    login_rate_window_seconds: int = 15 * 60
    lockout_threshold: int = 10
    lockout_minutes: int = 15
    unlock_rate_limit: int = 5

    # --- storage ------------------------------------------------------------
    storage_backend: Literal["s3", "local"] = "s3"
    local_storage_path: str = "/srv/data/media"
    s3_bucket: str = "pikirler-private"
    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    signed_url_ttl_seconds: int = 300
    upload_session_ttl_hours: int = 24
    max_image_bytes: int = 30 * 1024 * 1024
    max_video_bytes: int = 2 * 1024 * 1024 * 1024
    max_audio_bytes: int = 300 * 1024 * 1024
    max_document_bytes: int = 100 * 1024 * 1024
    thumbnail_size: int = 640

    # --- search / embeddings ------------------------------------------------
    embedding_provider: ProviderName = "disabled"
    embedding_model: str = ""
    embedding_dimensions: int = 768
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_is_local: bool = False
    embedding_timeout_seconds: float = 30.0
    embedding_max_chars: int = 8000
    # Instruction prefixes some models expect (EmbeddingGemma:
    # "task: search result | query: " and "title: none | text: ").
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""

    # --- LLM ----------------------------------------------------------------
    llm_provider: ProviderName = "disabled"
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_is_local: bool = False
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    ai_daily_request_limit: int = 200
    ai_context_max_thoughts: int = 20
    ai_context_max_chars_per_thought: int = 1500
    ai_context_max_total_chars: int = 16000

    # --- ASR ----------------------------------------------------------------
    asr_provider: AsrProviderName = "disabled"
    asr_base_url: str = ""
    asr_api_key: str = ""
    asr_model: str = ""
    asr_is_local: bool = True
    asr_timeout_seconds: float = 900.0
    asr_device: str = "auto"
    asr_compute_type: str = "auto"

    # --- jobs ---------------------------------------------------------------
    job_max_attempts: int = 5
    job_dispatch_interval_seconds: int = 10
    job_stale_after_seconds: int = 15 * 60
    soft_delete_retention_days: int = 30

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("default_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment == "production":
            if self.secret_key.lower() in _PLACEHOLDER_SECRETS or "change_me" in self.secret_key:
                raise ValueError("PIKIRLER_SECRET_KEY must be a real random secret in production")
            if not self.cookie_secure:
                raise ValueError("PIKIRLER_COOKIE_SECURE must be true in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
