"""SQLite engine setup and schema initialization.

MVP assumption (see docs/TECHNICAL_SPEC_v0.1.md): single-user, local
execution against one SQLite file. No distributed workers, no connection
pooling concerns beyond what SQLAlchemy's default pool already provides.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine

from app.storage.orm import Base

DEFAULT_DB_PATH = Path("data/motily.db")


def make_engine(db_path: Path | str = DEFAULT_DB_PATH) -> Engine:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", future=True)


def init_database(db_path: Path | str = DEFAULT_DB_PATH) -> Engine:
    """Create required tables if missing and return a ready-to-use engine.

    Idempotent: safe to call multiple times against the same file.
    """
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    return engine
