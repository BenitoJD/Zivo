from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET_KEYS = {"dev-secret-change-me"}
_DEV_CSRF_SECRET = "dev-csrf-change-me"


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

    session_days: int = 7
    session_remember_days: int = 30
    cookie_domain: str = ""

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:3000/api/auth/google/callback"
    frontend_url: str = "http://localhost:3000"

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

    @property
    def resolved_cookie_domain(self) -> str | None:
        """Cookie Domain so apex + www + api + auth share session."""
        raw = self.cookie_domain.strip().lstrip(".")
        if raw:
            return raw
        if not self.is_production:
            return None
        from urllib.parse import urlparse

        host = (urlparse(self.frontend_url).hostname or "").strip().lower()
        if not host or host in {"localhost", "127.0.0.1"} or host.endswith(".local"):
            return None
        parts = host.split(".")
        if len(parts) < 2:
            return None
        return ".".join(parts[-2:])

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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
