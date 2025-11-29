from __future__ import annotations

import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import Point, Polygon, MultiPolygon
from sqlalchemy import func, select

from app.models import Project, Station, User, VectorFeature, VectorLayer


@pytest.mark.usefixtures("db_session")
def test_polygon_intersects_point(db_session):
    """Insert sample data and verify ST_Intersects works for geometry queries."""
    owner = User(email="demo@example.com", password_hash="hash")
    project = Project(name="Demo Project", owner=owner)
    layer = VectorLayer(name="IBGE Sectors", source="tests", description="mock")

    station = Station(
        name="Test Station",
        project=project,
        service_type="FM",
        frequency_mhz=98.1,
        erp_kw=5.0,
        antenna_height_m=50.0,
        antenna_pattern={"azimuth": "omni"},
        location=from_shape(Point(-43.1000, -22.9000), srid=4326),
    )

    polygon = Polygon(
        [
            (-43.101, -22.901),
            (-43.099, -22.901),
            (-43.099, -22.899),
            (-43.101, -22.899),
            (-43.101, -22.901),
        ]
    )
    feature = VectorFeature(
        layer=layer,
        properties={"CD_SETOR": "1234567890"},
        geom=from_shape(MultiPolygon([polygon]), srid=4326),
    )

    db_session.add_all([owner, project, layer, station, feature])
    db_session.flush()

    # Query polygon hit using a point near the station.
    target_point = func.ST_SetSRID(func.ST_MakePoint(-43.1000, -22.9000), 4326)
    stmt = select(VectorFeature).where(func.ST_Intersects(VectorFeature.geom, target_point))
    result = db_session.execute(stmt).scalars().all()

    assert result, "Expected polygon to intersect with the target point."
    assert result[0].properties["CD_SETOR"] == "1234567890"
