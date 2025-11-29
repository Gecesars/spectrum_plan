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

from app.config import Base, build_engine, TestConfig
from app import create_app
from app.extensions import db


@pytest.fixture(scope="session")
def engine() -> Engine:
    db_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        db_url = "postgresql+psycopg2://postgres:postgres@localhost:5432/spectrum_test"
    engine = build_engine(db_url)
    if engine.dialect.name != "postgresql":
        # Spatial tests require PostGIS; keep engine available for potential unit tests.
        return engine
    try:
        with engine.begin() as conn:
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            except OperationalError as exc:  # PostGIS not installed in current host
                pytest.skip(f"PostGIS extension unavailable: {exc}")
        # Reset schema for isolated runs.
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    except OperationalError as exc:
        pytest.skip(f"Database unavailable: {exc}")
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


@pytest.fixture(scope="session")
def app(engine: Engine):
    # Point TestConfig to the prepared engine URL.
    os.environ["SQLALCHEMY_DATABASE_URI"] = str(engine.url)
    test_config = TestConfig
    application = create_app(test_config)
    application.config["TESTING"] = True
    with application.app_context():
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def runner(app):
    return app.test_cli_runner()
