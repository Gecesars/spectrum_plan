from __future__ import annotations

from celery import Celery
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

from app.config import Base

# Use our Declarative Base to keep metadata/schema consistent.
db = SQLAlchemy(model_class=Base)
migrate = Migrate()
celery = Celery("spectrum")
login_manager = LoginManager()


def init_extensions(app) -> None:
    """Initialize Flask extensions."""
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login_get"
    login_manager.session_protection = "strong"
    celery.conf.update(
        broker_url=app.config.get("CELERY_BROKER_URL"),
        result_backend=app.config.get("CELERY_RESULT_BACKEND"),
        task_track_started=True,
        timezone="UTC",
    )


@login_manager.user_loader
def load_user(user_id: str):
    from app.models import User

    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None
