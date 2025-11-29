from __future__ import annotations

from celery import Celery
from celery.utils.log import get_task_logger

from app.config import AppConfig, get_session
from app.core.propagation import calculate_coverage
from app.models import Simulation

config = AppConfig()
celery_app = Celery(
    "spectrum",
    broker=config.CELERY_BROKER_URL,
    backend=config.CELERY_RESULT_BACKEND,
)
celery_app.conf.update(task_track_started=True, timezone="UTC")

logger = get_task_logger(__name__)


@celery_app.task(bind=True, name="run_coverage_simulation")
def run_coverage_simulation(self, simulation_id: str, radius_km: float) -> dict:
    with get_session() as session:
        simulation = session.get(Simulation, simulation_id)
        if not simulation:
            raise ValueError(f"Simulation {simulation_id} not found")

        try:
            simulation.status = "RUNNING"
            session.flush()
            result = calculate_coverage(
                station_id=simulation.station_id,
                radius_km=radius_km,
                session=session,
            )
            simulation.status = "SUCCESS"
            simulation.result_path = result["image_path"]
            simulation.bbox_north = result["bbox"]["north"]
            simulation.bbox_south = result["bbox"]["south"]
            simulation.bbox_east = result["bbox"]["east"]
            simulation.bbox_west = result["bbox"]["west"]
            session.flush()
        except Exception as exc:  # noqa: BLE001 - propagate details to task state
            simulation.status = "FAILURE"
            session.flush()
            logger.exception("Simulation failed for %s", simulation_id)
            raise

        return {
            "image_path": simulation.result_path,
            "bbox": {
                "north": simulation.bbox_north,
                "south": simulation.bbox_south,
                "east": simulation.bbox_east,
                "west": simulation.bbox_west,
            },
        }
