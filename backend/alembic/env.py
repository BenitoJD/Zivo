from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db import Base
from app import models  # noqa: F401
from app.engine_runtime import pick

config = context.config
pick(
    config.config_file_name is not None,
    lambda: fileConfig(config.config_file_name),
    lambda: None,
)

target_metadata = Base.metadata
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# pgvector columns and partial/HNSW indexes are applied via raw SQL in migrations.
_RAW_SQL_OBJECTS = {
    "column": {"embedding"},
    "index": {
        "document_chunks_embedding_hnsw_idx",
        "ix_llm_response_cache_embedding_hnsw",
        "embedding_hnsw_idx",
        "uq_llm_models_default_per_kind",
    },
    "unique_constraint": {"uq_llm_providers_slug"},
}


def _include_object(object, name, type_, reflected, compare_to):
    raw = _RAW_SQL_OBJECTS.get(type_) or ()
    return name not in raw


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


pick(context.is_offline_mode(), run_migrations_offline, run_migrations_online)()
