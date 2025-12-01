from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


def load_terrain_profile(source: str) -> List[Dict[str, float]]:
    """Carrega perfil de terreno simples (JSON ou CSV)."""
    path = Path(source)
    if not path.exists():
        return []
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text())
    points: List[Dict[str, float]] = []
    for line in path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            km = float(parts[0])
            lat = float(parts[1])
            lon = float(parts[2])
            alt = float(parts[3]) if len(parts) > 3 else 0.0
        except ValueError:
            continue
        points.append({"dist_km": km, "lat": lat, "lon": lon, "alt_m": alt})
    return points
