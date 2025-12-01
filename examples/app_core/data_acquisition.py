
import io
import json
import math
import statistics
import time
import uuid
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from astropy import units as u
from flask import current_app
from pycraf import pathprof
from shapely.geometry import Polygon

from .models import Asset, AssetType, DatasetSource, DatasetSourceKind, db
from .storage import inline_asset_path

MAPBIOMAS_AVAILABLE_YEARS = list(range(1985, 2024))
_MAPBIOMAS_DEFAULT_YEAR = MAPBIOMAS_AVAILABLE_YEARS[-1]
MAPBIOMAS_BASE_URL = "https://storage.googleapis.com/mapbiomas-public/initiatives/brasil/collection_10/lulc/coverage"
RT3D_SCENE_MAX_CACHE_AGE_SECONDS = 6 * 3600  # 6h
DEFAULT_BUILDING_LEVEL_HEIGHT = 3.3  # m por andar


def _normalize_mapbiomas_year(year):
    if not MAPBIOMAS_AVAILABLE_YEARS:
        return None
    if year is None:
        return _MAPBIOMAS_DEFAULT_YEAR
    try:
        year = int(year)
    except (TypeError, ValueError):
        return _MAPBIOMAS_DEFAULT_YEAR
    min_year = MAPBIOMAS_AVAILABLE_YEARS[0]
    max_year = MAPBIOMAS_AVAILABLE_YEARS[-1]
    if year < min_year:
        return min_year
    if year > max_year:
        return max_year
    return year


def global_srtm_dir() -> Path:
    """
    Returns the shared SRTM directory (../SRTM relative to the project root).
    """
    base = Path(current_app.root_path).parent / "SRTM"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _hgt_tile_name(lat: float, lon: float) -> str:
    lat_floor = math.floor(lat)
    lon_floor = math.floor(lon)
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"{ns}{abs(lat_floor):02d}{ew}{abs(lon_floor):03d}"


def download_srtm_tile(project, lat, lon):
    """
    Garante a presença do tile SRTM1 (.hgt) baixado via viewpano (servidor usado pelo pycraf).
    """
    tile_name = _hgt_tile_name(lat, lon)
    global_dir = global_srtm_dir()
    tile_pattern = f"{tile_name}.hgt"

    try:
        matches = list(global_dir.rglob(tile_pattern))
        if not matches:
            with pathprof.SrtmConf.set(srtm_dir=str(global_dir), download='missing', server='viewpano'):
                pathprof.srtm_height_map(
                    lon * u.deg,
                    lat * u.deg,
                    0.02 * u.deg,
                    0.02 * u.deg,
                    map_resolution=1 * u.arcsec,
                )
            matches = list(global_dir.rglob(tile_pattern))
    except Exception as exc:
        current_app.logger.error("Falha ao baixar SRTM via viewpano: %s", exc)
        return None

    if not matches:
        current_app.logger.error("Tile %s não foi encontrado em %s após o download.", tile_name, global_dir)
        return None

    local_path = matches[0]

    existing = (
        Asset.query.filter_by(project_id=project.id, type=AssetType.dem)
        .order_by(Asset.created_at.desc())
        .first()
    )
    if existing and (existing.meta or {}).get('tile') == tile_name:
        return existing

    payload = local_path.read_bytes()
    file_size = len(payload)
    source = DatasetSource(
        project_id=project.id,
        kind=DatasetSourceKind.SRTM,
        locator={'server': 'viewpano'},
        notes=f"SRTM1 (1\") tile {tile_name} obtido do viewpano.",
    )
    db.session.add(source)
    db.session.flush()

    asset = Asset(
        project_id=project.id,
        type=AssetType.dem,
        path=inline_asset_path('dem', local_path.suffix or 'hgt'),
        mime_type='application/octet-stream',
        byte_size=file_size,
        data=payload,
        meta={'source': 'SRTM1 viewpano', 'tile': tile_name, 'resolution': '1 arc-second'},
        source_id=source.id,
    )
    db.session.add(asset)
    db.session.commit()
    return asset

def download_mapbiomas_tile(project, year):
    """
    Downloads a MapBiomas Collection 10 tile for a given year.
    """
    year = _normalize_mapbiomas_year(year)
    if year is None:
        return None
    tile_name = f"brazil_coverage_{year}.tif"
    url = f"{MAPBIOMAS_BASE_URL}/{tile_name}"

    existing = (
        Asset.query.filter_by(project_id=project.id, type=AssetType.lulc)
        .order_by(Asset.created_at.desc())
        .first()
    )
    if existing and (existing.meta or {}).get('year') == year:
        return existing

    current_app.logger.info(f"Downloading MapBiomas tile for {year} from {url}")

    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()

        # Stream to temp file to check size
        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    tmp_file.write(chunk)
            tmp_path = tmp_file.name

        file_size = os.path.getsize(tmp_path)
        MAX_DB_SIZE = 100 * 1024 * 1024  # 100 MB

        asset_data = None
        asset_path = inline_asset_path('lulc', 'tif')

        if file_size > MAX_DB_SIZE:
            # Offload to filesystem
            storage_root = os.environ.get('STORAGE_ROOT') or os.environ.get('LEGACY_STORAGE_ROOT')
            if not storage_root:
                # Fallback relative to app root
                storage_root = os.path.join(os.path.dirname(current_app.root_path), 'storage')
            
            blob_dir = os.path.join(storage_root, 'assets', 'large_blobs')
            os.makedirs(blob_dir, exist_ok=True)
            
            blob_filename = f"{uuid.uuid4().hex}.tif"
            dest_path = os.path.join(blob_dir, blob_filename)
            shutil.move(tmp_path, dest_path)
            
            # Use a file:// URI relative to storage root or absolute
            # We'll use a custom scheme or just store the relative path if the app handles it.
            # For now, let's use a file URI that indicates it's in the storage root.
            asset_path = f"file://assets/large_blobs/{blob_filename}"
            current_app.logger.info(f"MapBiomas tile too large for DB ({file_size} bytes). Stored at {asset_path}")
        else:
            # Small enough for DB
            with open(tmp_path, 'rb') as f:
                asset_data = f.read()
            os.remove(tmp_path)

        source = DatasetSource(
            project_id=project.id,
            kind=DatasetSourceKind.MAPBIOMAS,
            locator={'url': url},
            notes=f"MapBiomas Collection 10 tile for year {year}."
        )
        db.session.add(source)
        db.session.flush()

        asset = Asset(
            project_id=project.id,
            type=AssetType.lulc,
            path=asset_path,
            mime_type='image/tiff',
            byte_size=file_size,
            data=asset_data,
            meta={'source': 'MapBiomas Collection 10', 'year': year},
            source_id=source.id
        )
        db.session.add(asset)
        db.session.commit()

        current_app.logger.info("Successfully downloaded MapBiomas tile for %s.", year)
        return asset

    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Failed to download MapBiomas tile: {e}")
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)
        db.session.rollback()
        return None


def ensure_geodata_availability(project, latitude=None, longitude=None, lulc_year=None, fetch_lulc=True):
    """
    Garante que os dados básicos (DEM + LULC) estejam disponíveis para o projeto.

    Retorna um dicionário com os assets e metadados utilizados.
    """
    summary = {
        'dem_asset': None,
        'dem_dir': str(global_srtm_dir()),
        'lulc_asset': None,
        'lulc_year': None,
    }
    if project is None:
        return summary

    def _latest_asset(asset_type):
        return (
            Asset.query
            .filter_by(project_id=project.id, type=asset_type)
            .order_by(Asset.created_at.desc())
            .first()
        )

    dem_asset = None
    if latitude is not None and longitude is not None:
        try:
            dem_asset = download_srtm_tile(project, latitude, longitude)
        except Exception as exc:
            current_app.logger.warning(
                "geodata.dem.download_failed",
                extra={"project": getattr(project, "slug", None), "error": str(exc)},
            )
            dem_asset = None

    if dem_asset is None:
        dem_asset = _latest_asset(AssetType.dem)
    summary['dem_asset'] = dem_asset

    existing_lulc = _latest_asset(AssetType.lulc)
    if existing_lulc:
        summary['lulc_asset'] = existing_lulc
        summary['lulc_year'] = (existing_lulc.meta or {}).get('year')

    if fetch_lulc:
        target_year = _normalize_mapbiomas_year(lulc_year)
        if target_year is None:
            if summary['lulc_year'] is not None:
                target_year = _normalize_mapbiomas_year(summary['lulc_year'])
            else:
                current_year = datetime.utcnow().year - 1
                target_year = _normalize_mapbiomas_year(current_year)

        if target_year is not None:
            needs_download = (
                summary['lulc_asset'] is None
                or summary.get('lulc_year') != target_year
            )
            if needs_download:
                try:
                    lulc_asset = download_mapbiomas_tile(project, target_year)
                except Exception as exc:
                    current_app.logger.warning(
                        "geodata.lulc.download_failed",
                        extra={
                            "project": getattr(project, "slug", None),
                            "year": target_year,
                            "error": str(exc),
                        },
                    )
                    lulc_asset = None
                else:
                    if lulc_asset:
                        summary['lulc_asset'] = lulc_asset
                        summary['lulc_year'] = target_year

    return summary


def _coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounding_box(lat: float, lon: float, radius_km: float) -> Tuple[float, float, float, float]:
    radius_km = max(float(radius_km), 0.2)
    delta_lat = radius_km / 111.32
    cos_lat = math.cos(math.radians(lat))
    cos_lat = cos_lat if abs(cos_lat) > 1e-3 else 1e-3
    delta_lon = radius_km / (111.32 * cos_lat)
    south = max(-90.0, lat - delta_lat)
    north = min(90.0, lat + delta_lat)
    west = lon - delta_lon
    east = lon + delta_lon
    if west < -180.0:
        west += 360.0
    if east > 180.0:
        east -= 360.0
    return south, west, north, east


def _default_height_for_building(tags: Dict[str, str]) -> float:
    height_val = _coerce_float(tags.get('height'))
    if height_val:
        return max(2.5, min(height_val, 350.0))
    levels = _coerce_float(tags.get('building:levels') or tags.get('levels'))
    if levels:
        return max(3.0, min(levels * DEFAULT_BUILDING_LEVEL_HEIGHT, 350.0))
    building_type = (tags.get('building') or '').lower()
    if building_type in {'house', 'hut', 'garage'}:
        return 6.0
    if building_type in {'apartments', 'residential', 'dormitory'}:
        return 18.0
    if building_type in {'office', 'commercial', 'retail'}:
        return 22.0
    if building_type in {'industrial', 'warehouse'}:
        return 12.0
    if building_type in {'church', 'temple'}:
        return 15.0
    return 10.0


def _overpass_query(south: float, west: float, north: float, east: float) -> str:
    return f"""
    [out:json][timeout:60];
    (
        way["building"]({south},{west},{north},{east});
        relation["building"]({south},{west},{north},{east});
    );
    out body;
    >;
    out skel qt;
    """


def _fetch_overpass_buildings(lat: float, lon: float, radius_km: float) -> Optional[Dict]:
    south, west, north, east = _bounding_box(lat, lon, radius_km)
    query = _overpass_query(south, west, north, east)
    current_app.logger.info(
        "rt3d.scene.overpass.request",
        extra={"bbox": [south, west, north, east], "radius_km": radius_km},
    )
    try:
        resp = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=120,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        current_app.logger.warning("rt3d.scene.overpass.error: %s", exc)
        return None

    elements = payload.get("elements") or []
    nodes = {
        el["id"]: (el["lon"], el["lat"])
        for el in elements
        if el.get("type") == "node"
    }

    features = []
    points = []
    for el in elements:
        if el.get("type") != "way":
            continue
        node_ids = el.get("nodes") or []
        geometry = []
        for node_id in node_ids:
            coord = nodes.get(node_id)
            if coord:
                geometry.append(coord)
        if len(geometry) < 3:
            continue
        try:
            polygon = Polygon(geometry)
        except Exception:
            continue
        if polygon.is_empty:
            continue
        height = _default_height_for_building(el.get("tags") or {})
        centroid = polygon.centroid
        # Conversão aproximada graus->metros (válida para áreas pequenas)
        area = polygon.area * (111_320 ** 2)
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [list(map(list, polygon.exterior.coords))],
            },
            "properties": {
                "height_m": height,
                "source": "osm-overpass",
                "centroid_lat": centroid.y,
                "centroid_lon": centroid.x,
                "footprint_area": area,
                "tags": el.get("tags") or {},
            },
        }
        features.append(feature)
        points.append(
            {
                "lat": centroid.y,
                "lon": centroid.x,
                "height_m": height,
            }
        )

    if not features:
        current_app.logger.info(
            "rt3d.scene.overpass.empty",
            extra={"bbox": [south, west, north, east]},
        )
        return None

    median_height = statistics.median(pt["height_m"] for pt in points)
    return {
        "type": "FeatureCollection",
        "features": features,
        "summary": {
            "points": points,
            "median_height": median_height,
            "bbox": [south, west, north, east],
            "source": "osm-overpass",
        },
    }


def _write_scene_asset(project, scene_data: Dict, radius_km: float):
    summary = scene_data.setdefault("summary", {})
    summary.setdefault("radius_km", radius_km)
    payload = json.dumps(scene_data).encode("utf-8")

    source = DatasetSource(
        project_id=project.id,
        kind=DatasetSourceKind.OSM,
        locator={"service": "overpass"},
        notes=f"Building footprints via Overpass (radius={radius_km} km).",
    )
    db.session.add(source)
    db.session.flush()

    asset = Asset(
        project_id=project.id,
        type=AssetType.building_footprints,
        path=f"inline://buildings/{uuid.uuid4().hex}.geojson",
        mime_type="application/geo+json",
        byte_size=len(payload),
        data=payload,
        meta={
            "radius_km": radius_km,
            "source": "osm-overpass",
            "feature_count": len(scene_data.get("features", [])),
        },
        source_id=source.id,
    )
    db.session.add(asset)
    db.session.commit()
    return asset, asset.path


def _load_cached_scene(project):
    asset = (
        Asset.query.filter_by(project_id=project.id, type=AssetType.building_footprints)
        .order_by(Asset.created_at.desc())
        .first()
    )
    if not asset:
        return None
    if asset.created_at:
        age = (datetime.utcnow() - asset.created_at).total_seconds()
        if age > RT3D_SCENE_MAX_CACHE_AGE_SECONDS:
            return None
    payload = None
    if asset.data:
        payload = asset.data
    if not payload:
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception:
        return None
    summary = data.get("summary") or {}
    if not summary.get("points"):
        return None
    return {
        "source": summary.get("source", "osm-overpass"),
        "points": summary.get("points"),
        "median_height": summary.get("median_height"),
        "asset_path": asset.path,
        "asset_id": str(asset.id),
        "feature_count": len(data.get("features", [])),
        "generated_at": datetime.utcnow().isoformat(),
        "cache": True,
        "origin": summary.get("origin"),
        "radius_km": summary.get("radius_km"),
    }


def ensure_rt3d_scene(
    project,
    latitude: Optional[float],
    longitude: Optional[float],
    radius_km: Optional[float] = None,
    api_key: Optional[str] = None,
) -> Optional[Dict]:
    """
    Garantimos uma cena urbana (footprints + alturas) para o motor RT3D.
    Prioriza Photorealistic 3D Tiles (quando habilitado); caso indisponível,
    cai para Overpass/OSM. Resultado: summary com pontos e metadados.
    """
    if project is None or latitude is None or longitude is None:
        return None

    radius_km = max(float(radius_km or 3.0), 0.5)
    cached = _load_cached_scene(project)
    if cached:
        current_app.logger.info(
            "rt3d.scene.cache_hit",
            extra={"project": project.slug, "feature_count": cached["feature_count"]},
        )
        cached.setdefault("origin", {"lat": latitude, "lon": longitude})
        cached.setdefault("radius_km", radius_km)
        return cached

    # Placeholder for future Google Photorealistic Tiles integration.
    if api_key:
        try:
            resp = requests.get(
                "https://tile.googleapis.com/v1/3dtiles/root.json",
                params={"key": api_key, "map": "photorealistic"},
                timeout=10,
            )
            if resp.status_code == 200:
                current_app.logger.info(
                    "rt3d.scene.google_placeholder",
                    extra={"project": project.slug},
                )
        except Exception as exc:
            current_app.logger.debug("rt3d.scene.google_probe_failed: %s", exc)

    overpass_scene = _fetch_overpass_buildings(latitude, longitude, radius_km)
    if not overpass_scene:
        return None

    summary = overpass_scene.setdefault("summary", {})
    summary["origin"] = {"lat": latitude, "lon": longitude}
    summary["radius_km"] = radius_km

    asset, rel_path = _write_scene_asset(project, overpass_scene, radius_km)
    summary = overpass_scene["summary"]
    points = summary.get("points") or []
    if len(points) > 800:
        stride = max(1, len(points) // 800)
        points = points[::stride]
    result = {
        "source": "osm-overpass",
        "origin": {"lat": latitude, "lon": longitude},
        "radius_km": radius_km,
        "asset_path": rel_path,
        "asset_id": str(asset.id),
        "feature_count": len(overpass_scene.get("features", [])),
        "points": points,
        "median_height": summary.get("median_height"),
        "generated_at": datetime.utcnow().isoformat(),
    }
    current_app.logger.info(
        "rt3d.scene.ready",
        extra={"project": project.slug, "feature_count": result["feature_count"]},
    )
    return result
