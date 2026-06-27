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
    csrf_disabled: bool = False
    trusted_proxy_ips: str = ""
    rate_limit_per_minute: int = 60
    hsts_max_age_seconds: int = 31_536_000
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
    # MCQ reuse across documents with identical page text: all | demo | off
    mcq_reuse_scope: str = "all"
    # Open-world tutor loop (one PR, three independent switches — all default off
    # so production behaviour is unchanged until each is enabled deliberately).
    # 1. Allow a page to honestly yield ZERO questions (cover pages, TOCs, junk,
    #    non-content) instead of forcing the old minimum-5 floor.
    allow_zero_questions: bool = False
    # 2. Let generation adapt its question style + truth model to the material's
    #    content_type (expository/narrative/argumentative/procedural/reference).
    content_aware_generation: bool = False
    # 3. How the next question is chosen: "sequence" (legacy, by generation order),
    #    "concept_reinforce" (adaptive — reacts to the last answer), or
    #    "difficulty_edge" (targets the productive-struggle band from calibration).
    selection_policy: str = "sequence"
    # 4. Calibrate per-item difficulty + per-learner ability from real answer
    #    outcomes (online Elo → intel.projection). Off by default so the moat fills
    #    only when enabled; "difficulty_edge" selection needs this on to have data.
    calibration_enabled: bool = False
    # 5. Seed a birth-time difficulty PRIOR per item at generation time (cold-start
    #    fix for answered-once items, so difficulty_edge has a non-coin-flip starting
    #    point). Off the answer path; outcomes correct it. Off by default until the
    #    real-data prior gate holds.
    difficulty_prior_enabled: bool = False
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dimension: int = 384
    rerank_enabled: bool = True
    # Tutor chat skips cross-encoder rerank by default — learn mode already pins the
    # current page; reranking the full RAG window was adding seconds of CPU latency.
    rerank_chat_enabled: bool = False
    # Smallest FastEmbed cross-encoder (0.08 GB ONNX); CPU-friendly MS MARCO reranker.
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"

    # Practice library — Wikidata + Wikipedia backbones for the concept graph.
    wikidata_api_base: str = "https://www.wikidata.org/w/api.php"
    wikipedia_api_base: str = "https://en.wikipedia.org/api/rest_v1"
    # When false, the public practice endpoints return 404 (kill switch).
    practice_enabled: bool = True

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
        if self.minio_access_key == "zivo" or self.minio_secret_key == "zivo-secret":
            raise ValueError("MINIO credentials must be changed when ENVIRONMENT != 'development'")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
