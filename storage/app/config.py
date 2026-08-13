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
    frontend_url: str = "http://localhost:3000"

    minio_endpoint: str = "localhost:9020"
    minio_access_key: str = "zivo"
    minio_secret_key: str = "zivo-secret"
    minio_bucket: str = "zivo-artifacts"
    minio_secure: bool = False
    minio_public_endpoint: str = ""
    minio_public_secure: bool | None = None

    max_upload_bytes: int = _GB

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]
        frontend = self.frontend_url.strip().rstrip("/")
        if frontend and frontend not in origins:
            origins.append(frontend)
        return origins

    @property
    def is_production(self) -> bool:
        return self.app_env == "production" or self.environment == "production"

    @field_validator("minio_public_secure", mode="before")
    @classmethod
    def _empty_public_secure(cls, value: object) -> object:
        if value == "":
            return None
        return value

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
        if self.csrf_disabled:
            raise ValueError("CSRF_DISABLED cannot be true when ENVIRONMENT != 'development'")
        if self.minio_access_key == "zivo" or self.minio_secret_key == "zivo-secret":
            raise ValueError("MINIO credentials must be set when ENVIRONMENT != 'development'")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
