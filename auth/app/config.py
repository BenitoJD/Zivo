from functools import lru_cache
from urllib.parse import urlparse

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.engine_runtime import Pred, Rule, apply, first_match, pick

_DEV_SECRET_KEYS = {"dev-secret-change-me"}
_DEV_CSRF_SECRET = "dev-csrf-change-me"


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

    session_days: int = 7
    session_remember_days: int = 30
    cookie_domain: str = ""

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:3000/api/auth/google/callback"
    frontend_url: str = "http://localhost:3000"

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

    @property
    def resolved_cookie_domain(self) -> str | None:
        """Cookie Domain so apex + www + api + auth share session."""
        raw = self.cookie_domain.strip().lstrip(".")

        def _from_frontend() -> str | None:
            host = (urlparse(self.frontend_url).hostname or "").strip().lower()
            parts = host.split(".")
            blocked = (
                not host
                or host in {"localhost", "127.0.0.1"}
                or host.endswith(".local")
                or len(parts) < 2
            )
            return pick(blocked, lambda: None, lambda: ".".join(parts[-2:]))

        def _derived() -> str | None:
            return pick(not self.is_production, lambda: None, _from_frontend)

        return pick(bool(raw), lambda: raw, _derived)

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
