from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET_KEYS = {"dev-secret-change-me"}
_DEV_CSRF_SECRET = "dev-csrf-change-me"


_GB = 1024 * 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://zivo:zivo@localhost:5455/zivo"
    secret_key: str = "dev-secret-change-me"
    csrf_secret: str = "dev-csrf-change-me"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    minio_endpoint: str = "localhost:9020"
    minio_access_key: str = "zivo"
    minio_secret_key: str = "zivo-secret"
    minio_bucket: str = "zivo-artifacts"
    minio_secure: bool = False
    minio_public_endpoint: str = ""
    minio_public_secure: bool | None = None

    litellm_model: str = "openai/glm-4.7"
    openai_api_base: str = ""
    openai_api_key: str = ""
    zai_api_base: str = "https://api.z.ai/api/coding/paas/v4"
    zai_api_key: str = ""
    # Step Fun (stepfun.com) — OpenAI-compatible. step-3.5-flash is a fast,
    # non-reasoning model well-suited to high-volume MCQ generation.
    stepfun_api_base: str = "https://api.stepfun.ai/step_plan/v1"
    stepfun_api_key: str = ""
    openrouter_api_base: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    # When true, chat/MCQ calls without an explicit model_id rotate across all
    # configured chat models and fail over on transient provider errors.
    llm_pool_enabled: bool = True
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dimension: int = 384
    rerank_enabled: bool = True
    # Smallest FastEmbed cross-encoder (0.08 GB ONNX); CPU-friendly MS MARCO reranker.
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"

    session_days: int = 7
    session_remember_days: int = 30
    guest_document_limit: int = 1
    guest_message_limit: int = 30
    daily_message_limit: int = 100
    max_upload_bytes: int = _GB
    storage_limit_bytes: int = _GB

    @field_validator("minio_public_secure", mode="before")
    @classmethod
    def _empty_public_secure_is_none(cls, value: object) -> object:
        if value == "" or value is None:
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production" or self.environment == "production"

    @property
    def minio_presign_endpoint(self) -> str:
        return self.minio_public_endpoint.strip() or self.minio_endpoint

    @property
    def minio_presign_secure(self) -> bool:
        if self.minio_public_secure is not None:
            return self.minio_public_secure
        return self.minio_secure

    @model_validator(mode="after")
    def _reject_dev_secrets_outside_development(self) -> "Settings":
        if self.environment == "development":
            return self
        if self.secret_key in _DEV_SECRET_KEYS:
            raise ValueError("SECRET_KEY must be set when ENVIRONMENT != 'development'")
        if self.csrf_secret == _DEV_CSRF_SECRET:
            raise ValueError("CSRF_SECRET must be set when ENVIRONMENT != 'development'")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
