from __future__ import annotations

from pathlib import Path

from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.models import Project, Station, User
from app.regulatory.diffraction import calculate_interference_matrix
from app.regulatory.regulatory import RegulatoryStandard


class FlatProvider:
    def get_elevation_profile(self, lat_list, lon_list):
        return [10 for _ in lat_list]


def test_fm_adjacent_station_viability(tmp_path, monkeypatch, db_session):
    # Redirect output directory to isolate test artifacts.
    monkeypatch.setattr(
        "app.regulatory.diffraction.OUTPUT_DIR", tmp_path, raising=False
    )

    owner = User(email="rf@example.com", password_hash="hash")
    project = Project(name="Reg", owner=owner)

    station_a = Station(
        name="Station A",
        project=project,
        service_type="FM",
        frequency_mhz=98.1,
        erp_kw=5.0,
        antenna_height_m=30.0,
        antenna_pattern={"azimuth": "omni"},
        location=from_shape(Point(0.0, 0.0), srid=4326),
    )
    # ~15 km north (0.135 deg latitude)
    station_b = Station(
        name="Station B",
        project=project,
        service_type="FM",
        frequency_mhz=98.3,
        erp_kw=3.0,
        antenna_height_m=25.0,
        antenna_pattern={"azimuth": "omni"},
        location=from_shape(Point(0.0, 0.135), srid=4326),
    )
    db_session.add_all([owner, project, station_a, station_b])
    db_session.flush()

    result = calculate_interference_matrix(
        victim=station_a,
        interferer=station_b,
        radius_km=20.0,
        session=db_session,
        provider=FlatProvider(),
        standard=RegulatoryStandard(),
        resolution_m=500,
    )

    assert Path(result["heatmap_path"]).exists()
    assert result["required_pr"] == 6.0
