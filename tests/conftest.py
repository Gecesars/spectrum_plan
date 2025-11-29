from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import OperationalError

# Ensure the repo root is on sys.path for test imports.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Base, build_engine


@pytest.fixture(scope="session")
def engine() -> Engine:
    db_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        db_url = "postgresql+psycopg2://postgres:postgres@localhost:5432/spectrum_test"
    engine = build_engine(db_url)
    if engine.dialect.name != "postgresql":
        # Spatial tests require PostGIS; keep engine available for potential unit tests.
        return engine
    with engine.begin() as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        except OperationalError as exc:  # PostGIS not installed in current host
            pytest.skip(f"PostGIS extension unavailable: {exc}")
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def db_session(engine: Engine) -> Session:
    if engine.dialect.name != "postgresql":
        pytest.skip("PostGIS-backed database is required for spatial tests.")
    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(
        bind=connection, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
