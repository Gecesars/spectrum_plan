from __future__ import annotations

from http import HTTPStatus
import json
from typing import Any, Dict

from flask import Blueprint, jsonify, request, send_from_directory
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import cast, func, select
from sqlalchemy.types import Integer

from app.config import get_session
from app.models import Project, ProjectArtifact, Simulation, Station, VectorFeature, VectorLayer
from app.tasks import run_coverage_simulation

core_bp = Blueprint("core", __name__)


def _tile_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Convert slippy tile coordinates to WGS84 bbox."""
    n = 2.0**z
    lon_w = x / n * 360.0 - 180.0
    lon_e = (x + 1) / n * 360.0 - 180.0
    lat_n = func.degrees(func.atan(func.sinh(func.pi() * (1 - 2 * y / n))))  # type: ignore
    lat_s = func.degrees(func.atan(func.sinh(func.pi() * (1 - 2 * (y + 1) / n))))  # type: ignore
    return lon_w, lat_s, lon_e, lat_n


def _tile_bbox_py(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Pure-Python slippy tile to bbox."""
    import math

    n = 2.0 ** z
    lon_w = x / n * 360.0 - 180.0
    lon_e = (x + 1) / n * 360.0 - 180.0
    lat_rad_n = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_rad_s = math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n)))
    return lon_w, math.degrees(lat_rad_s), lon_e, math.degrees(lat_rad_n)


@core_bp.get("/health")
def health():
    return jsonify({"status": "ok"})


@core_bp.post("/project/<int:project_id>/station")
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

        lat = float(payload["lat"])
        lon = float(payload["lon"])
        station = Station(
            name=payload["name"],
            project=project,
            station_type=payload.get("service_type", "FM"),
            status="Proposed",
            latitude=lat,
            longitude=lon,
            site_elevation=float(payload.get("site_elevation", 0.0)),
            frequency_mhz=float(payload["frequency_mhz"]),
            erp_kw=float(payload["erp_kw"]),
            antenna_height=float(payload["antenna_height_m"]),
            antenna_pattern=payload.get("antenna_pattern", {}),
            location=from_shape(Point(lon, lat), srid=4326),
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


@core_bp.post("/simulation/start")
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
            project_id=station.project_id,
            station_id=station.id,
            status="QUEUED",
        )
        session.add(simulation)
        session.flush()

        async_result = run_coverage_simulation.delay(simulation.id, radius_km)
        simulation.task_id = async_result.id
        session.flush()

        return jsonify({"simulation_id": simulation.id, "task_id": async_result.id}), HTTPStatus.ACCEPTED


@core_bp.get("/simulation/<string:simulation_id>/status")
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


@core_bp.get("/analytics/population")
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


@core_bp.get("/analytics/summary")
def analytics_summary():
    with get_session() as session:
        sector_count = session.execute(select(func.count(VectorFeature.id))).scalar_one()
        layer_count = session.execute(select(func.count(func.distinct(VectorFeature.layer_id)))).scalar_one()
        project_count = session.execute(select(func.count(Project.id))).scalar_one()
        sim_count = session.execute(select(func.count(Simulation.id))).scalar_one()
        artifact_count = session.execute(select(func.count(ProjectArtifact.id))).scalar_one()
    return jsonify(
        {
            "sectors": sector_count,
            "layers": layer_count,
            "projects": project_count,
            "simulations": sim_count,
            "artifacts": artifact_count,
        }
    )


@core_bp.get("/dashboard/summary")
def dashboard_summary():
    with get_session() as session:
        project_count = session.execute(select(func.count(Project.id))).scalar_one()
        simulation_count = session.execute(select(func.count(Simulation.id))).scalar_one()
        artifact_count = session.execute(select(func.count(ProjectArtifact.id))).scalar_one()
    return jsonify(
        {
            "total_projects": project_count,
            "total_simulations": simulation_count,
            "total_artifacts": artifact_count,
        }
    )


@core_bp.get("/outputs/<path:filename>")
def get_output_file(filename: str):
    # Serve generated PNGs (coverage/interference) from outputs folder.
    return send_from_directory("app/outputs", filename, as_attachment=False)


@core_bp.get("/user/me")
def current_user():
    # Placeholder user info; replace with real auth integration as needed.
    return jsonify(
        {
            "name": "RF Engineer",
            "email": "user@example.com",
            "days_left": 30,
        }
    )


@core_bp.get("/tiles/<string:layer_name>/<int:z>/<int:x>/<int:y>")
def tile_query(layer_name: str, z: int, x: int, y: int):
    """Return GeoJSON features intersecting a slippy tile for a given layer."""
    limit = max(1, min(int(request.args.get("limit", 500)), 2000))
    bbox = _tile_bbox_py(z, x, y)
    with get_session() as session:
        layer = session.query(VectorLayer).filter_by(name=layer_name).first()
        if not layer:
            return jsonify({"error": f"Layer {layer_name} not found"}), HTTPStatus.NOT_FOUND

        envelope = func.ST_MakeEnvelope(bbox[0], bbox[1], bbox[2], bbox[3], 4326)
        stmt = (
            select(
                VectorFeature.id,
                VectorFeature.properties,
                func.ST_AsGeoJSON(VectorFeature.geom),
            )
            .where(VectorFeature.layer_id == layer.id)
            .where(func.ST_Intersects(VectorFeature.geom, envelope))
            .limit(limit)
        )
        rows = session.execute(stmt).all()

    features = []
    for fid, props, geom_json in rows:
        features.append(
            {
                "type": "Feature",
                "id": fid,
                "properties": props,
                "geometry": json.loads(geom_json) if geom_json else None,
            }
        )
    return jsonify({"type": "FeatureCollection", "features": features})
