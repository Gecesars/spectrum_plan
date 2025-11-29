from __future__ import annotations

from pathlib import Path

from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.core.propagation import calculate_coverage
from app.models import Project, Station, User


class MockElevationProvider:
    def get_elevation_profile(self, lat_list, lon_list):
        return [0 for _ in lat_list]


def test_calculate_coverage_creates_png(tmp_path, monkeypatch, db_session):
    # Redirect output directory for test isolation.
    monkeypatch.setattr(
        "app.core.propagation.OUTPUT_DIR", tmp_path, raising=False
    )

    owner = User(email="cover@example.com", password_hash="hash")
    project = Project(name="Coverage", owner=owner)
    station = Station(
        name="Station A",
        project=project,
        station_type="FM",
        status="Proposed",
        latitude=-22.0,
        longitude=-43.0,
        frequency_mhz=100.0,
        erp_kw=1.0,
        antenna_height=30.0,
        antenna_pattern={"azimuth": "omni"},
        location=from_shape(Point(-43.0, -22.0), srid=4326),
    )
    db_session.add_all([owner, project, station])
    db_session.flush()

    result = calculate_coverage(
        station_id=station.id,
        radius_km=1.0,
        grid_size=10,
        session=db_session,
        elevation_provider=MockElevationProvider(),
    )

    image_path = Path(result["image_path"])
    assert image_path.exists()
    assert set(result["bbox"].keys()) == {"north", "south", "east", "west"}
