from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker

# Load environment variables from a local .env when available to simplify dev setup.
load_dotenv()


class Base(DeclarativeBase):
    """Declarative base for all ORM models (schema enforced as public)."""

    metadata = MetaData(schema="public")


class Config:
    """Default Flask configuration."""

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "SQLALCHEMY_DATABASE_URI",
        os.getenv("DATABASE_URL", "postgresql+psycopg2://user:password@localhost:5432/spectrum_db"),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")


class DevConfig(Config):
    """Development flavor."""

    DEBUG = True
    SQLALCHEMY_ECHO = False


class TestConfig(Config):
    """Testing configuration."""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "TEST_DATABASE_URI",
        os.getenv("SQLALCHEMY_DATABASE_URI", "postgresql+psycopg2://postgres:postgres@localhost:5432/spectrum_test"),
    )


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
        connect_args={"options": "-c search_path=public"},
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
    MAIL_SERVER: str = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT: int = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USERNAME: Optional[str] = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD: Optional[str] = os.getenv("MAIL_PASSWORD")
    MAIL_USE_TLS: bool = os.getenv("MAIL_USE_TLS", "True").lower() == "true"
    MAIL_DEFAULT_SENDER: str = os.getenv("MAIL_DEFAULT_SENDER", "noreply@spectrum.com")


def init_db(bind: Optional[Engine] = None) -> None:
    """Create all tables based on ORM metadata."""
    # Import models to ensure metadata is populated before create_all.
    from app import models  # noqa: F401

    target = bind or engine
    with target.begin() as conn:
        conn.execute(text("SET search_path TO public"))
        Base.metadata.create_all(bind=conn)
