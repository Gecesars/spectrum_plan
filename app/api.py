from __future__ import annotations

from http import HTTPStatus
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request, send_from_directory
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import cast, func, select
from sqlalchemy.types import Integer

from app.config import get_session
from app.models import Project, Simulation, Station, VectorFeature
from app.tasks import run_coverage_simulation

api_bp = Blueprint("api", __name__)


@api_bp.get("/health")
def health():
    return jsonify({"status": "ok"})


@api_bp.post("/project/<int:project_id>/station")
def create_station(project_id: int):
    payload: Dict[str, Any] = request.get_json(force=True)
    required = ["name", "frequency_mhz", "erp_kw", "antenna_height_m", "lat", "lon"]
    missing = [field for field in required if field not in payload]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), HTTPStatus.BAD_REQUEST

    with get_session() as session:
        project = session.get(Project, project_id)
        if not project:
            return jsonify({"error": "Project not found"}), HTTPStatus.NOT_FOUND

        station = Station(
            name=payload["name"],
            project=project,
            service_type=payload.get("service_type", "FM"),
            frequency_mhz=float(payload["frequency_mhz"]),
            erp_kw=float(payload["erp_kw"]),
            antenna_height_m=float(payload["antenna_height_m"]),
            antenna_pattern=payload.get("antenna_pattern", {}),
            location=from_shape(Point(float(payload["lon"]), float(payload["lat"])), srid=4326),
        )
        session.add(station)
        session.flush()

        return (
            jsonify(
                {
                    "station_id": station.id,
                    "project_id": project_id,
                }
            ),
            HTTPStatus.CREATED,
        )


@api_bp.post("/simulation/start")
def start_simulation():
    payload = request.get_json(force=True)
    station_id = payload.get("station_id")
    radius_km = float(payload.get("radius_km", 30.0))
    if not station_id:
        return jsonify({"error": "station_id is required"}), HTTPStatus.BAD_REQUEST

    with get_session() as session:
        station = session.get(Station, station_id)
        if not station:
            return jsonify({"error": "Station not found"}), HTTPStatus.NOT_FOUND

        simulation = Simulation(
            station_id=station.id,
            status="QUEUED",
        )
        session.add(simulation)
        session.flush()

        async_result = run_coverage_simulation.delay(simulation.id, radius_km)
        simulation.task_id = async_result.id
        session.flush()

        return jsonify({"simulation_id": simulation.id, "task_id": async_result.id}), HTTPStatus.ACCEPTED


@api_bp.get("/simulation/<string:simulation_id>/status")
def simulation_status(simulation_id: str):
    with get_session() as session:
        simulation = session.get(Simulation, simulation_id)
        if not simulation:
            return jsonify({"error": "Simulation not found"}), HTTPStatus.NOT_FOUND

        response = {
            "simulation_id": simulation.id,
            "status": simulation.status,
            "result_path": simulation.result_path,
            "bbox": {
                "north": simulation.bbox_north,
                "south": simulation.bbox_south,
                "east": simulation.bbox_east,
                "west": simulation.bbox_west,
            }
            if simulation.result_path
            else None,
        }
        return jsonify(response)


@api_bp.get("/analytics/population")
def analytics_population():
    simulation_id = request.args.get("simulation_id")
    if not simulation_id:
        return jsonify({"error": "simulation_id query param is required"}), HTTPStatus.BAD_REQUEST

    with get_session() as session:
        simulation = session.get(Simulation, simulation_id)
        if not simulation:
            return jsonify({"error": "Simulation not found"}), HTTPStatus.NOT_FOUND
        if not simulation.result_path or not all(
            [simulation.bbox_north, simulation.bbox_south, simulation.bbox_east, simulation.bbox_west]
        ):
            return jsonify({"error": "Simulation not complete"}), HTTPStatus.BAD_REQUEST

        envelope = func.ST_MakeEnvelope(
            simulation.bbox_west,
            simulation.bbox_south,
            simulation.bbox_east,
            simulation.bbox_north,
            4326,
        )

        population_sum = func.sum(cast(VectorFeature.properties["population"].astext, Integer))
        household_sum = func.sum(cast(VectorFeature.properties["households"].astext, Integer))

        stmt = select(population_sum, household_sum).where(func.ST_Intersects(VectorFeature.geom, envelope))
        result = session.execute(stmt).one_or_none()

        total_population = int(result[0] or 0) if result else 0
        households = int(result[1] or 0) if result else 0

        return jsonify({"total_population": total_population, "households": households})


@api_bp.get("/analytics/summary")
def analytics_summary():
    with get_session() as session:
        sector_count = session.execute(select(func.count(VectorFeature.id))).scalar_one()
        layer_count = session.execute(select(func.count(func.distinct(VectorFeature.layer_id)))).scalar_one()
    return jsonify({"sectors": sector_count, "layers": layer_count})


@api_bp.get("/outputs/<path:filename>")
def get_output_file(filename: str):
    # Serve generated PNGs (coverage/interference) from outputs folder.
    return send_from_directory("app/outputs", filename, as_attachment=False)


@api_bp.get("/user/me")
def current_user():
    # Placeholder user info; replace with real auth integration as needed.
    return jsonify(
        {
            "name": "RF Engineer",
            "email": "user@example.com",
            "days_left": 30,
        }
    )
