from __future__ import annotations

import math
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from geoalchemy2.shape import to_shape
from geopy.distance import geodesic
from sqlalchemy.orm import Session

from app.config import get_session
from app.core.terrain import ElevationProvider
from app.models import Station

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


@contextmanager
def _session_scope(session: Optional[Session] = None):
    if session is not None:
        yield session
    else:
        with get_session() as scoped:
            yield scoped


def fspl(distance_km: float, freq_mhz: float) -> float:
    """Free Space Path Loss in dB."""
    if distance_km <= 0:
        distance_km = 0.001
    return 32.44 + 20 * math.log10(distance_km) + 20 * math.log10(freq_mhz)


def erp_kw_to_dbm(erp_kw: float) -> float:
    # ERP is provided in kW; convert to dBm for link budget math.
    watts = erp_kw * 1000.0
    return 10 * math.log10(watts * 1000.0)


def calculate_coverage(
    station_id: int,
    radius_km: float,
    grid_size: int = 100,
    session: Optional[Session] = None,
    elevation_provider: Optional[ElevationProvider] = None,
) -> Dict:
    """Compute coverage heatmap and return path + bounding box; designed for Celery tasks."""
    provider = elevation_provider or ElevationProvider()
    with _session_scope(session) as db:
        station = db.get(Station, station_id)
        if not station:
            raise ValueError(f"Station id={station_id} not found")
        station_point = to_shape(station.location)
        center_lat = station_point.y
        center_lon = station_point.x

    delta_lat = radius_km / 111.0
    delta_lon = radius_km / (111.0 * max(math.cos(math.radians(center_lat)), 0.01))

    lats = np.linspace(center_lat - delta_lat, center_lat + delta_lat, grid_size)
    lons = np.linspace(center_lon - delta_lon, center_lon + delta_lon, grid_size)
    field_strength = np.full((grid_size, grid_size), np.nan)

    tx_dbm = erp_kw_to_dbm(station.erp_kw)
    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            dist_km = geodesic((center_lat, center_lon), (lat, lon)).km
            if dist_km > radius_km:
                continue
            try:
                elevs = provider.get_elevation_profile([center_lat, lat], [center_lon, lon])
                gradient_loss = max(0.0, (elevs[1] - elevs[0]) / max(dist_km * 1000.0, 1.0))
            except FileNotFoundError:
                gradient_loss = 0.0
            path_loss = fspl(dist_km, station.frequency_mhz) + gradient_loss
            rx_dbm = tx_dbm - path_loss
            e_field = rx_dbm + 20 * math.log10(station.frequency_mhz) + 77.2
            field_strength[i, j] = e_field

    masked = np.ma.array(field_strength, mask=np.isnan(field_strength))
    plt.figure(figsize=(6, 6))
    cmap = plt.cm.inferno
    cmap.set_bad(alpha=0.0)  # transparent where outside radius
    plt.imshow(
        masked,
        extent=(lons.min(), lons.max(), lats.min(), lats.max()),
        origin="lower",
        cmap=cmap,
        alpha=0.7,
    )
    plt.axis("off")
    output_path = OUTPUT_DIR / f"coverage_station_{station_id}.png"
    plt.savefig(output_path, bbox_inches="tight", pad_inches=0, transparent=True)
    plt.close()

    bbox = {
        "north": center_lat + delta_lat,
        "south": center_lat - delta_lat,
        "east": center_lon + delta_lon,
        "west": center_lon - delta_lon,
    }
    return {"image_path": str(output_path), "bbox": bbox}
