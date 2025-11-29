from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List

import numpy as np

# HGT tiles are 1x1 degree grids stored as big-endian 16-bit integers.
# We infer side length to allow small mocked tiles in tests.


class ElevationProvider:
    def __init__(self, srtm_root: str | Path = "SRTM", cache_size: int = 8) -> None:
        self.srtm_root = Path(srtm_root)
        self._load_tile = lru_cache(maxsize=cache_size)(self._load_tile)  # type: ignore

    def get_elevation_profile(self, lat_list: Iterable[float], lon_list: Iterable[float]) -> List[int]:
        """Return elevations for paired lists of lat/lon coordinates."""
        lats = list(lat_list)
        lons = list(lon_list)
        if len(lats) != len(lons):
            raise ValueError("lat_list and lon_list must have the same length.")
        return [self._point_elevation(lat, lon) for lat, lon in zip(lats, lons)]

    def _point_elevation(self, lat: float, lon: float) -> int:
        tile_path, lat_base, lon_base = self._tile_info(lat, lon)
        data = self._load_tile(str(tile_path))
        size = data.shape[0]

        row = round((lat_base + 1 - lat) * (size - 1))
        col = round((lon - lon_base) * (size - 1))
        if row < 0 or row >= size or col < 0 or col >= size:
            raise ValueError(f"Coordinate {lat}, {lon} outside tile bounds.")
        return int(data[row, col])

    def _tile_info(self, lat: float, lon: float) -> tuple[Path, int, int]:
        lat_base = math.floor(lat)
        lon_base = math.floor(lon)
        lat_prefix = "N" if lat_base >= 0 else "S"
        lon_prefix = "E" if lon_base >= 0 else "W"
        tile_name = f"{lat_prefix}{abs(lat_base):02d}{lon_prefix}{abs(lon_base):03d}.hgt"
        tile_path = self.srtm_root / tile_name
        if not tile_path.exists():
            raise FileNotFoundError(f"SRTM tile not found: {tile_path}")
        return tile_path, lat_base, lon_base

    def _load_tile(self, tile_path: str) -> np.ndarray:
        path = Path(tile_path)
        data = np.fromfile(path, dtype=">i2")
        side = int(math.sqrt(data.size))
        if side * side != data.size:
            raise ValueError(f"Invalid HGT file size for {path}")
        return data.reshape((side, side))

