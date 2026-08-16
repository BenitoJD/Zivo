from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.engine_runtime import Pred, Rule, apply, choose, first_match, pick

_DEV_SECRET_KEYS = {"dev-secret-change-me"}
_DEV_CSRF_SECRET = "dev-csrf-change-me"
_GB = 1024 * 1024 * 1024


def _raise(exc: BaseException) -> None:
    raise exc


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
        origins = [o.strip().rstrip("/") for o in filter(None, map(str.strip, self.cors_origins.split(",")))]
        frontend = self.frontend_url.strip().rstrip("/")

        def _append() -> list[str]:
            origins.append(frontend)
            return origins

        return pick(bool(frontend) and frontend not in origins, _append, lambda: origins)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production" or self.environment == "production"

    @field_validator("minio_public_secure", mode="before")
    @classmethod
    def _empty_public_secure(cls, value: object) -> object:
        return choose(value == "", None, value)

    @property
    def minio_presign_endpoint(self) -> str:
        return self.minio_public_endpoint.strip() or self.minio_endpoint

    @property
    def minio_presign_secure(self) -> bool:
        return choose(self.minio_public_secure is not None, self.minio_public_secure, self.minio_secure)

    @model_validator(mode="after")
    def _reject_dev_secrets_outside_development(self) -> "Settings":
        def _check() -> Settings:
            apply(
                first_match(
                    (
                        Rule(when=(Pred("bad_secret", "truthy"),), action="secret"),
                        Rule(when=(Pred("bad_csrf", "truthy"),), action="csrf"),
                        Rule(when=(Pred("csrf_off", "truthy"),), action="csrf_disabled"),
                        Rule(when=(), action="ok"),
                    ),
                    {
                        "bad_secret": self.secret_key in _DEV_SECRET_KEYS,
                        "bad_csrf": self.csrf_secret == _DEV_CSRF_SECRET,
                        "csrf_off": self.csrf_disabled,
                    },
                ).action,
                {
                    "secret": lambda: _raise(
                        ValueError("SECRET_KEY must be set when ENVIRONMENT != 'development'")
                    ),
                    "csrf": lambda: _raise(
                        ValueError("CSRF_SECRET must be set when ENVIRONMENT != 'development'")
                    ),
                    "csrf_disabled": lambda: _raise(
                        ValueError("CSRF_DISABLED cannot be true when ENVIRONMENT != 'development'")
                    ),
                    "ok": lambda: None,
                },
            )
            return self

        return pick(self.environment == "development", lambda: self, _check)


@lru_cache
def get_settings() -> Settings:
    return Settings()
