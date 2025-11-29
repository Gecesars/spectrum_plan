from __future__ import annotations

from celery import Celery
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

from app.config import Base

# Use our Declarative Base to keep metadata/schema consistent.
db = SQLAlchemy(model_class=Base)
migrate = Migrate()
celery = Celery("spectrum")


def init_extensions(app) -> None:
    """Initialize Flask extensions."""
    db.init_app(app)
    migrate.init_app(app, db)
    celery.conf.update(
        broker_url=app.config.get("CELERY_BROKER_URL"),
        result_backend=app.config.get("CELERY_RESULT_BACKEND"),
        task_track_started=True,
        timezone="UTC",
    )
