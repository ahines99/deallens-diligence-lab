"""Database engine, session factory, and schema initialization."""
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.config import settings

# For SQLite, ensure the target directory exists and allow cross-thread use
# (FastAPI + uvicorn use a threadpool for sync endpoints).
connect_args: dict = {}
if settings.is_sqlite:
    connect_args = {"check_same_thread": False}
    # DATABASE_URL like sqlite:///./data/deallens.sqlite3 -> ensure ./data exists.
    db_path = settings.database_url.split("///", 1)[-1]
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)


if settings.is_sqlite:
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        """SQLite disables FK enforcement per connection unless it is explicitly enabled."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_db() -> None:
    """Create metadata directly for isolated tests only; production uses Alembic."""
    from src import models  # noqa: F401  (registers all model classes)
    from src.db.base import Base

    Base.metadata.create_all(bind=engine)


def migrate_db() -> None:
    """Upgrade the configured database to the repository's explicit Alembic head."""
    from alembic import command
    from alembic.config import Config

    api_root = Path(__file__).resolve().parents[2]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def prepare_schema() -> None:
    """Apply the configured, explicit schema lifecycle policy."""
    mode = settings.schema_management.strip().lower()
    if mode == "migrate":
        migrate_db()
    elif mode == "create_all":
        init_db()
    elif mode != "external":
        raise RuntimeError(
            "SCHEMA_MANAGEMENT must be 'migrate', 'external', or test-only 'create_all'"
        )


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
