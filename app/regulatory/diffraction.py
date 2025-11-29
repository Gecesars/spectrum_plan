from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from geoalchemy2.shape import to_shape
from geopy.distance import geodesic
from sqlalchemy import cast, func, select
from sqlalchemy.types import Integer
from sqlalchemy.orm import Session

from app.core.propagation import erp_kw_to_dbm, fspl
from app.core.terrain import ElevationProvider
from app.models import Station, VectorFeature
from app.regulatory.contours import _freq_offset
from app.regulatory.regulatory import RegulatoryStandard

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def knife_edge_loss(v: float) -> float:
    if v < -0.7:
        return 0.0
    return 6.9 + 20 * math.log10(math.sqrt((v - 0.1) ** 2 + 1) + v - 0.1)


def deygout_loss(heights: list[float], tx_height_m: float, rx_height_m: float, freq_mhz: float, distance_m: float) -> float:
    if len(heights) < 3 or distance_m <= 0:
        return 0.0
    n = len(heights)
    step = distance_m / (n - 1)
    wavelength = 300.0 / freq_mhz

    positions = [i * step for i in range(n)]
    baseline = [
        tx_height_m + (rx_height_m - tx_height_m) * (pos / distance_m) for pos in positions
    ]

    v_values: list[float] = []
    for i in range(1, n - 1):
        d1 = positions[i]
        d2 = distance_m - d1
        h_clearance = heights[i] - baseline[i]
        if d1 <= 0 or d2 <= 0:
            continue
        v = h_clearance * math.sqrt((2 / wavelength) * ((1 / d1) + (1 / d2)))
        v_values.append(v)

    if not v_values:
        return 0.0
    v_values.sort(reverse=True)
    loss = 0.0
    for v in v_values[:3]:
        loss += knife_edge_loss(v)
    return loss


def _sample_profile(provider: ElevationProvider, lat1: float, lon1: float, lat2: float, lon2: float, samples: int = 16) -> list[int]:
    lats = np.linspace(lat1, lat2, samples)
    lons = np.linspace(lon1, lon2, samples)
    return provider.get_elevation_profile(lats, lons)


def _link_loss(provider: ElevationProvider, station: Station, lat: float, lon: float) -> tuple[float, float]:
    shape = to_shape(station.location)
    dist_km = geodesic((shape.y, shape.x), (lat, lon)).km
    profile = _sample_profile(provider, shape.y, shape.x, lat, lon)
    distance_m = dist_km * 1000.0
    tx_ground = profile[0]
    rx_ground = profile[-1]
    profile[0] = tx_ground + station.antenna_height_m
    profile[-1] = rx_ground + 1.5  # nominal receive height
    diff_loss = deygout_loss(profile, profile[0], profile[-1], station.frequency_mhz, distance_m)
    total_loss = fspl(max(dist_km, 0.001), station.frequency_mhz) + diff_loss
    rx_dbm = erp_kw_to_dbm(station.erp_kw) - total_loss
    e_field = rx_dbm + 20 * math.log10(station.frequency_mhz) + 77.2
    return dist_km, e_field


def calculate_interference_matrix(
    victim: Station,
    interferer: Station,
    radius_km: float,
    session: Session,
    resolution_m: int = 100,
    provider: Optional[ElevationProvider] = None,
    standard: Optional[RegulatoryStandard] = None,
) -> dict:
    """Vectorized margin map using FSPL + simplified Deygout."""
    provider = provider or ElevationProvider()
    standard = standard or RegulatoryStandard()
    freq_offset = _freq_offset(victim, interferer)
    required_pr = standard.get_required_pr(victim.service_type, freq_offset)

    victim_shape = to_shape(victim.location)
    interferer_shape = to_shape(interferer.location)

    center_lat = victim_shape.y
    center_lon = victim_shape.x
    span_km = radius_km * 2
    grid_size = max(10, min(200, int((span_km * 1000) / resolution_m)))

    # Build grid
    delta_lat = radius_km / 111.0
    delta_lon = radius_km / (111.0 * max(math.cos(math.radians(center_lat)), 0.01))
    lats = np.linspace(center_lat - delta_lat, center_lat + delta_lat, grid_size)
    lons = np.linspace(center_lon - delta_lon, center_lon + delta_lon, grid_size)
    margin_map = np.full((grid_size, grid_size), np.nan)

    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            dist_victim = geodesic((center_lat, center_lon), (lat, lon)).km
            if dist_victim > radius_km:
                continue
            try:
                _, wanted_field = _link_loss(provider, victim, lat, lon)
                _, unwanted_field = _link_loss(provider, interferer, lat, lon)
            except FileNotFoundError:
                # Fallback to FSPL-only when SRTM tile is missing
                dist_int = geodesic((interferer_shape.y, interferer_shape.x), (lat, lon)).km
                wanted_field = erp_kw_to_dbm(victim.erp_kw) - fspl(max(dist_victim, 0.001), victim.frequency_mhz)
                wanted_field += 20 * math.log10(victim.frequency_mhz) + 77.2
                unwanted_field = erp_kw_to_dbm(interferer.erp_kw) - fspl(max(dist_int, 0.001), interferer.frequency_mhz)
                unwanted_field += 20 * math.log10(interferer.frequency_mhz) + 77.2

            margin = (wanted_field - unwanted_field) - required_pr
            margin_map[i, j] = margin

    violations = margin_map < 0
    masked = np.ma.array(margin_map, mask=np.isnan(margin_map))
    plt.figure(figsize=(6, 6))
    cmap = plt.cm.Reds
    cmap.set_bad(alpha=0.0)
    plt.imshow(
        masked,
        extent=(lons.min(), lons.max(), lats.min(), lats.max()),
        origin="lower",
        cmap=cmap,
        alpha=0.6,
    )
    plt.axis("off")
    heatmap_path = OUTPUT_DIR / f"interference_{victim.id}_{interferer.id}.png"
    plt.savefig(heatmap_path, bbox_inches="tight", pad_inches=0, transparent=True)
    plt.close()

    cell_area_km2 = (resolution_m / 1000.0) ** 2
    impacted_area_km2 = float(np.nansum(violations) * cell_area_km2)

    bbox = {
        "north": center_lat + delta_lat,
        "south": center_lat - delta_lat,
        "east": center_lon + delta_lon,
        "west": center_lon - delta_lon,
    }
    envelope = func.ST_MakeEnvelope(bbox["west"], bbox["south"], bbox["east"], bbox["north"], 4326)
    population_sum = func.sum(cast(VectorFeature.properties["population"].astext, Integer))
    result = session.execute(
        select(population_sum).where(func.ST_Intersects(VectorFeature.geom, envelope))
    ).scalar_one_or_none()
    impacted_population = int(result or 0)

    return {
        "heatmap_path": str(heatmap_path),
        "bbox": bbox,
        "impacted_area_km2": impacted_area_km2,
        "impacted_population": impacted_population,
        "required_pr": required_pr,
    }
