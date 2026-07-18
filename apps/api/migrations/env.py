from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src import models  # noqa: F401 - register every mapped table
from src.config import settings
from src.db.base import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Hide the G83 pgvector fast-path objects from autogenerate/check.

    ``document_chunks.embedding_vector`` (+ its partial backfill index) exists ONLY on
    PostgreSQL databases where migration d5f2b8c3a1e9 found the pgvector extension. It is a
    derived cache of the JSON ``embedding`` column and is deliberately absent from the model
    metadata, so SQLite/create_all schemas never grow it. Without this filter,
    ``alembic check`` on a migrated Postgres database would propose dropping the column.
    """
    if type_ == "column" and name == "embedding_vector" and obj.table.name == "document_chunks":
        return False
    if type_ == "index" and name == "ix_document_chunks_vector_backfill":
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=settings.is_sqlite,
        include_object=include_object,
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
            compare_type=True,
            render_as_batch=settings.is_sqlite,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
