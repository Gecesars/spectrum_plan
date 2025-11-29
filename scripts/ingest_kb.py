from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

import geopandas as gpd
import pandas as pd
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_session
from app.models import VectorFeature, VectorLayer

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_PATH = BASE_DIR / "etl_errors.log"

logger = logging.getLogger("etl")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.FileHandler(LOG_PATH)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


@contextmanager
def _session_scope(session: Optional[Session] = None) -> Iterable[Session]:
    if session is not None:
        yield session
    else:
        with get_session() as scoped:
            yield scoped


def ingest_ibge_vectors(shp_path: str | Path, layer_name: str = "IBGE Sectors", session: Optional[Session] = None) -> dict:
    """Ingest IBGE shapefile into VectorFeature with CRS normalized to EPSG:4326."""
    shp_path = Path(shp_path)
    gdf = gpd.read_file(shp_path)
    if gdf.empty:
        return {"inserted": 0, "skipped": 0}

    if gdf.crs is None:
        raise ValueError("Shapefile must declare a CRS.")
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    if "CD_SETOR" not in gdf.columns:
        raise ValueError("Expected column 'CD_SETOR' in shapefile.")

    inserted = 0
    skipped = 0

    with _session_scope(session) as db:
        layer = db.query(VectorLayer).filter_by(name=layer_name).one_or_none()
        if not layer:
            layer = VectorLayer(name=layer_name, source=str(shp_path))
            db.add(layer)
            db.flush()

        for _, row in gdf.iterrows():
            geom: BaseGeometry = row.geometry
            if geom is None or geom.is_empty or not geom.is_valid:
                skipped += 1
                logger.warning("Invalid geometry skipped for CD_SETOR=%s", row.get("CD_SETOR"))
                continue

            if isinstance(geom, Polygon):
                geom = MultiPolygon([geom])

            properties = row.drop(labels=gdf.geometry.name).to_dict()
            properties["CD_SETOR"] = str(properties.get("CD_SETOR")).zfill(15)
            feature = VectorFeature(
                layer=layer,
                properties=properties,
                geom=from_shape(geom, srid=4326),
            )
            db.add(feature)
            inserted += 1

        if session is None:
            db.commit()
        else:
            db.flush()

    return {"inserted": inserted, "skipped": skipped}


def ingest_demographic_csv(csv_path: str | Path, session: Optional[Session] = None) -> int:
    """Merge demographic CSV into VectorFeature properties using CD_SETOR key."""
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path)
    if "CD_SETOR" not in df.columns:
        raise ValueError("Expected column 'CD_SETOR' in CSV.")

    updated = 0
    with _session_scope(session) as db:
        for _, row in df.iterrows():
            cd_setor = str(row["CD_SETOR"]).zfill(15)
            payload = {k: v for k, v in row.items() if k != "CD_SETOR"}
            payload_json = json.dumps(payload)
            candidates = {cd_setor, cd_setor.lstrip("0") or "0"}
            for candidate in candidates:
                result = db.execute(
                    text(
                        """
                        UPDATE vector_features
                        SET properties = properties || CAST(:payload AS jsonb)
                        WHERE btrim(properties->>'CD_SETOR') = :cd_setor
                        """
                    ),
                    {"payload": payload_json, "cd_setor": candidate},
                )
                updated += result.rowcount or 0

        if session is None:
            db.commit()

    return updated


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest Knowledge Base datasets.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    shp_parser = subparsers.add_parser("shp", help="Ingest IBGE shapefile")
    shp_parser.add_argument("path", help="Path to .shp file")
    shp_parser.add_argument("--layer", default="IBGE Sectors", help="Vector layer name")

    csv_parser = subparsers.add_parser("csv", help="Merge demographic CSV")
    csv_parser.add_argument("path", help="Path to CSV with CD_SETOR and demographic columns")

    args = parser.parse_args()

    if args.command == "shp":
        stats = ingest_ibge_vectors(args.path, layer_name=args.layer)
        print(f"Ingest complete: {stats}")
    elif args.command == "csv":
        count = ingest_demographic_csv(args.path)
        print(f"Updated {count} features")
