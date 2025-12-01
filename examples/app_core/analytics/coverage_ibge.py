from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from app_core.integrations import ibge as ibge_api

LOGGER = logging.getLogger(__name__)



@dataclass
class MunicipalityCoverage:
    ibge_code: str
    municipality: str
    state: str
    state_id: Optional[str]
    max_field_dbuvm: float
    sample_lat: float
    sample_lon: float
    points: int = 0
    tile_hits: int = 0
    population: Optional[float] = None
    population_year: Optional[int] = None
    income_per_capita: Optional[float] = None
    income_year: Optional[int] = None


def _parse_signal_dict(signal_dict: Dict[str, float], min_dbuv: float) -> List[Tuple[float, float, float]]:
    points: List[Tuple[float, float, float]] = []
    for key, value in signal_dict.items():
        try:
            val = float(value)
        except (TypeError, ValueError):
            continue
        if val < min_dbuv:
            continue
        lat_str, lon_str = key.strip("()").split(",")
        try:
            lat = float(lat_str)
            lon = float(lon_str)
        except ValueError:
            continue
        points.append((lat, lon, val))
    return points


def _tile_center_latlon(x_idx: int, y_idx: int, zoom: int) -> Tuple[float, float]:
    scale = 1 << zoom
    lon_deg = (x_idx + 0.5) / scale * 360.0 - 180.0
    n = math.pi - (2.0 * math.pi * (y_idx + 0.5) / scale)
    lat_rad = math.atan(math.sinh(n))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


def _extract_tile_stats(summary_data: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    stats = summary_data.get("tile_stats") or {}
    if not stats:
        tiles_payload = summary_data.get("tiles") or {}
        if isinstance(tiles_payload, dict):
            stats = tiles_payload.get("stats") or tiles_payload.get("tile_stats") or {}
    return stats if isinstance(stats, dict) else {}


def _collect_tile_points(tile_stats: Dict[str, Dict[str, object]]) -> Tuple[List[Tuple[float, float, float]], Optional[int]]:
    normalized: Dict[int, Dict[str, object]] = {}
    for zoom_key, bucket in tile_stats.items():
        try:
            zoom = int(zoom_key)
        except (TypeError, ValueError):
            continue
        if isinstance(bucket, dict) and bucket:
            normalized[zoom] = bucket
    if not normalized:
        return [], None
    selected_zoom = max(normalized.keys())
    points: List[Tuple[float, float, float]] = []
    for tile_key, value in normalized[selected_zoom].items():
        try:
            x_str, y_str = tile_key.split("/")
            x_idx = int(x_str)
            y_idx = int(y_str)
            tile_value = float(value)
        except (ValueError, TypeError, AttributeError):
            continue
        lat, lon = _tile_center_latlon(x_idx, y_idx, selected_zoom)
        points.append((lat, lon, tile_value))
    return points, selected_zoom


def _cluster_points(points: List[Tuple[float, float, float]], precision: int = 2, limit: int = 400) -> List[Tuple[float, float, float, int]]:
    clusters: Dict[Tuple[float, float], Dict[str, float]] = {}
    for lat, lon, value in points:
        key = (round(lat, precision), round(lon, precision))
        cluster = clusters.setdefault(key, {"lat": lat, "lon": lon, "value": value, "count": 0})
        cluster["count"] += 1
        if value > cluster["value"]:
            cluster["lat"] = lat
            cluster["lon"] = lon
            cluster["value"] = value
    cluster_list = [
        (info["lat"], info["lon"], info["value"], info["count"]) for info in clusters.values()
    ]
    cluster_list.sort(key=lambda item: item[2], reverse=True)
    if limit and len(cluster_list) > limit:
        return cluster_list[:limit]
    return cluster_list


def _resolve_municipality(lat: float, lon: float) -> Optional[Dict[str, str]]:
    try:
        detail = ibge_api.reverse_geocode_offline(lat, lon)
    except ibge_api.ReverseGeocoderUnavailable as exc:
        LOGGER.warning("coverage_ibge.geocode_unavailable", extra={"error": str(exc)})
        return None
    if not detail or not detail.get("name"):
        return None

    state_hint = detail.get("state_code") or detail.get("state")
    code = ibge_api.resolve_municipality_code(detail.get("name"), state_hint)
    if not code:
        entry = ibge_api.find_local_municipality(detail.get("name"), state_hint)
        if entry:
            code = entry.get("code")
    if not code:
        return None
    entry = ibge_api.get_local_municipality_entry(code)
    if not entry:
        return None
    return {
        "ibge_code": str(code),
        "municipality": entry.get("name") or detail.get("name"),
        "state": entry.get("state") or detail.get("state"),
        "state_id": entry.get("state_code"),
    }


def _enrich_municipalities_with_ibge(
    municipalities: Dict[str, MunicipalityCoverage],
    population_threshold: float = 25.0,
) -> None:
    if not municipalities:
        return

    for code, info in municipalities.items():
        entry = ibge_api.get_local_municipality_entry(code)
        if not entry:
            continue
        population = entry.get("population")
        if population is not None:
            info.population = population
            info.population_year = entry.get("population_year")
        income = entry.get("income_per_capita")
        if income is not None:
            info.income_per_capita = income
            info.income_year = entry.get("income_year")
        state_code = entry.get("state_code")
        if state_code:
            info.state_id = state_code


def summarize_coverage_demographics(
    summary_json_path: Path | None = None,
    min_field_dbuvm: float = 25.0,
    cluster_precision: int = 2,
    cluster_limit: int = 400,
    summary_payload: Optional[Dict] = None,
) -> Dict[str, object]:
    if summary_payload is not None:
        summary_data = dict(summary_payload)
    else:
        if summary_json_path is None or not summary_json_path.exists():
            raise FileNotFoundError(f"Coverage summary not found: {summary_json_path}")
        with summary_json_path.open("r", encoding="utf-8") as handle:
            summary_data = json.load(handle)

    tile_stats = _extract_tile_stats(summary_data)
    tile_points, tile_zoom = _collect_tile_points(tile_stats)
    tiles_total = len(tile_points) or None
    tile_hits = [point for point in tile_points if point[2] >= min_field_dbuvm]
    tiles_covered = len(tile_hits) if tile_points else None

    points_source = "tiles" if tile_points else "signal_dict"
    signal_dict = summary_data.get("signal_level_dict") or {}
    signal_points_total = len(signal_dict) or None
    points = tile_hits if tile_hits else []

    if not points:
        points = _parse_signal_dict(signal_dict, min_field_dbuvm)
        if points:
            points_source = "signal_dict"
        else:
            return {
                "threshold_dbuv": min_field_dbuvm,
                "total_pixels": 0,
                "cluster_count": 0,
                "municipalities": [],
                "sample_source": points_source,
                "tile_zoom": tile_zoom,
                "tiles_total": tiles_total,
                "tiles_covered": tiles_covered,
                "signal_points_total": signal_points_total,
                "population_covered": 0,
                "municipality_count": 0,
            }

    clusters = _cluster_points(points, precision=cluster_precision, limit=cluster_limit)
    municipalities: Dict[str, MunicipalityCoverage] = {}

    uses_tiles = points_source == "tiles"

    for lat, lon, value, count in clusters:
        meta = _resolve_municipality(lat, lon)
        if not meta:
            continue
        code = meta["ibge_code"]
        municipality = municipalities.get(code)
        if not municipality:
            municipality = MunicipalityCoverage(
                ibge_code=code,
                municipality=meta.get("municipality") or "",
                state=meta.get("state") or "",
                state_id=meta.get("state_id"),
                max_field_dbuvm=value,
                sample_lat=lat,
                sample_lon=lon,
                points=count,
            )
            municipalities[code] = municipality
        else:
            municipality.points += count
            if value > municipality.max_field_dbuvm:
                municipality.max_field_dbuvm = value
                municipality.sample_lat = lat
                municipality.sample_lon = lon
        if uses_tiles:
            municipality.tile_hits += count

    _enrich_municipalities_with_ibge(municipalities)

    ordered = sorted(
        municipalities.values(),
        key=lambda item: item.max_field_dbuvm,
        reverse=True,
    )

    payload = []
    population_total = 0.0
    for info in ordered:
        if info.population:
            population_total += float(info.population)
        payload.append(
            {
                "ibge_code": info.ibge_code,
                "municipality": info.municipality,
                "state": info.state,
                "max_field_dbuvm": round(info.max_field_dbuvm, 2),
                "sample_lat": info.sample_lat,
                "sample_lon": info.sample_lon,
                "points": info.points,
                "tile_hits": info.tile_hits,
                "population": info.population,
                "population_year": info.population_year,
                "income_per_capita": info.income_per_capita,
                "income_year": info.income_year,
            }
        )

    municipality_count = len(payload)
    population_value = population_total if population_total > 0 else None

    return {
        "threshold_dbuv": min_field_dbuvm,
        "total_pixels": len(points),
        "cluster_count": len(clusters),
        "municipalities": payload,
        "sample_source": points_source,
        "tile_zoom": tile_zoom,
        "tiles_total": tiles_total,
        "tiles_covered": tiles_covered,
        "signal_points_total": signal_points_total,
        "population_covered": population_value,
        "municipality_count": municipality_count,
    }
