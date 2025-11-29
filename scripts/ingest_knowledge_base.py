from __future__ import annotations

"""
ETL for Phase 1: ingest IBGE shapefiles, merge attributes, and parse Anatel XML.
This wraps the lower-level helpers from scripts.ingest_kb for consistency with the spec.
"""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy.orm import Session

from app.config import get_session
from app.models import Project, Station, User
from scripts.ingest_kb import ingest_demographic_csv, ingest_ibge_vectors

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _ensure_system_project(session: Session) -> Project:
    user = session.query(User).first()
    if not user:
        user = User(email="system@example.com", password_hash="stub", full_name="System")
        session.add(user)
        session.flush()
    project = session.query(Project).filter_by(name="BASE NACIONAL").first()
    if not project:
        project = Project(name="BASE NACIONAL", description="Carga Anatel/IBGE", user_id=user.id)
        session.add(project)
        session.flush()
    return project


def _parse_coord(raw: str) -> float:
    try:
        return float(str(raw).replace(",", "."))
    except Exception as exc:
        raise ValueError(f"Invalid coordinate: {raw}") from exc


def ingest_anatel_xml(xml_path: str, session: Optional[Session] = None) -> int:
    """Parse Anatel XML and store as Stations in project BASE NACIONAL (defaults to FM)."""
    xml_path = Path(xml_path)
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    count = 0
    with (session or get_session()) as db:
        project = _ensure_system_project(db)
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for elem in root.findall(".//estacao"):
            freq = elem.findtext("frequencia")
            lat = elem.findtext("latitude")
            lon = elem.findtext("longitude")
            erp = elem.findtext("erp")
            name = elem.findtext("entidade") or "Anatel Station"
            try:
                station = Station(
                    project=project,
                    name=name,
                    station_type="FM",
                    status="Existing",
                    frequency_mhz=_parse_coord(freq),
                    erp_kw=_parse_coord(erp) if erp else 0.0,
                    latitude=_parse_coord(lat),
                    longitude=_parse_coord(lon),
                    antenna_height=30.0,
                    antenna_pattern={},
                    location=from_shape(Point(_parse_coord(lon), _parse_coord(lat)), srid=4326),
                )
                db.add(station)
                count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to ingest station: %s", exc)
        if session is None:
            db.commit()
    return count


def ingest_ibge_shapefile(shp_path: str, layer_name: str = "IBGE Setores") -> dict:
    """Wrapper around ingest_ibge_vectors to match Phase 1 naming."""
    return ingest_ibge_vectors(shp_path, layer_name=layer_name)


def merge_ibge_attributes(csv_path: str, session: Optional[Session] = None) -> int:
    """Wrapper merging CSV attributes into vector_features."""
    return ingest_demographic_csv(csv_path, session=session)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest Knowledge Base datasets.")
    parser.add_argument("--anatel-xml", help="Path to Anatel XML (plano_basicoTVFM.xml)")
    parser.add_argument("--ibge-shp", help="Path to IBGE shapefile")
    parser.add_argument("--ibge-csv", help="Path to IBGE CSV with CD_SETOR and attributes")
    args = parser.parse_args()

    if args.anatel_xml:
        logger.info("Ingesting Anatel XML: %s", args.anatel_xml)
        count = ingest_anatel_xml(args.anatel_xml)
        logger.info("Ingested %s stations", count)
    if args.ibge_shp:
        logger.info("Ingesting IBGE shapefile: %s", args.ibge_shp)
        stats = ingest_ibge_shapefile(args.ibge_shp)
        logger.info("Ingested shapefile stats: %s", stats)
    if args.ibge_csv:
        logger.info("Merging IBGE CSV: %s", args.ibge_csv)
        updated = merge_ibge_attributes(args.ibge_csv)
        logger.info("Updated rows: %s", updated)
