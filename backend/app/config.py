from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://zivo:zivo@localhost:5455/zivo"
    minio_endpoint: str = "localhost:9020"
    minio_access_key: str = "zivo"
    minio_secret_key: str = "zivo-secret"
    minio_bucket: str = "zivo-artifacts"
    minio_secure: bool = False


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
