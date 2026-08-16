from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.engine_runtime import pick

config = context.config
pick(
    config.config_file_name is not None,
    lambda: fileConfig(config.config_file_name),
    lambda: None,
)

target_metadata = None
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

VERSION_TABLE = "alembic_version_practice"


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table=VERSION_TABLE,
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
            version_table=VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()


pick(context.is_offline_mode(), run_migrations_offline, run_migrations_online)
