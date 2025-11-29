from __future__ import annotations

from celery import Celery
from flask import Flask


def make_celery(app: Flask) -> Celery:
    """Bind Celery to Flask context using broker/backends from config."""
    celery = Celery(
        app.import_name,
        broker=app.config.get("CELERY_BROKER_URL"),
        backend=app.config.get("CELERY_RESULT_BACKEND"),
    )
    celery.conf.update(task_track_started=True, timezone="UTC")

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask  # type: ignore
    return celery


# Simple debug task for Phase 1 validation.
def register_debug_task(celery: Celery) -> None:
    @celery.task(name="debug_add")
    def debug_add(x: int, y: int) -> int:
        return x + y

