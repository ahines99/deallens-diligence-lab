"""Database engine, session factory, and schema initialization."""
from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
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

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_db() -> None:
    """Create all tables. Import models so they register on the metadata."""
    from src import models  # noqa: F401  (registers all model classes)
    from src.db.base import Base

    Base.metadata.create_all(bind=engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
