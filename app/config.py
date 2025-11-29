from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker

# Load environment variables from a local .env when available to simplify dev setup.
load_dotenv()


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _default_db_url() -> str:
    # Prefer explicit env var; fall back to a Postgres URL to keep parity with production.
    return os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/spectrum",
    )


def build_engine(db_url: Optional[str] = None, echo: bool = False) -> Engine:
    """Create a SQLAlchemy Engine with sane defaults."""
    return create_engine(
        db_url or _default_db_url(),
        echo=echo,
        future=True,
        pool_pre_ping=True,
    )


# Global engine/session for app runtime; tests can inject their own engine.
engine: Engine = build_engine()
SessionLocal = scoped_session(
    sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
)


@contextmanager
def get_session():
    """Provide a transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass
class AppConfig:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key")
    DATABASE_URL: str = _default_db_url()
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", REDIS_URL)
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)


def init_db(bind: Optional[Engine] = None) -> None:
    """Create all tables based on ORM metadata."""
    target = bind or engine
    Base.metadata.create_all(bind=target)
