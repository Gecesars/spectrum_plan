from __future__ import annotations

import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
from sqlalchemy import select

from app.models import VectorFeature
from scripts.ingest_kb import ingest_demographic_csv, ingest_ibge_vectors


def test_ingest_ibge_and_merge_demographics(tmp_path, db_session):
    polygon = Polygon(
        [
            (-43.0, -22.0),
            (-42.9, -22.0),
            (-42.9, -21.9),
            (-43.0, -21.9),
            (-43.0, -22.0),
        ]
    )

    gdf = gpd.GeoDataFrame(
        {"CD_SETOR": ["001"], "foo": ["bar"], "geometry": [polygon]},
        crs="EPSG:4326",
    )
    shp_path = tmp_path / "sector.shp"
    gdf.to_file(shp_path)

    stats = ingest_ibge_vectors(shp_path, session=db_session)
    assert stats["inserted"] == 1

    csv_path = tmp_path / "demo.csv"
    pd.DataFrame([{"CD_SETOR": "001", "population": 1234}]).to_csv(csv_path, index=False)
    updated = ingest_demographic_csv(csv_path, session=db_session)
    assert updated == 1

    feature = db_session.execute(select(VectorFeature)).scalar_one()
    assert feature.properties["CD_SETOR"] == "001"
    assert feature.properties["population"] == 1234
