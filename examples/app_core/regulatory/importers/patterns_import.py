from __future__ import annotations

import io
import math
from typing import Dict, List, Sequence, Tuple


def parse_pattern_csv(raw: str) -> List[Tuple[float, float]]:
    """Interpreta um arquivo simples HRP/VRP (angulo, ganho)."""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    pairs: List[Tuple[float, float]] = []
    for line in lines:
        if line.startswith("#"):
            continue
        parts = line.replace(";", ",").split(",")
        if len(parts) < 2:
            continue
        try:
            angle = float(parts[0])
            gain = float(parts[1])
        except ValueError:
            continue
        pairs.append((angle, gain))
    return pairs


def summarize_pattern(points: Sequence[Tuple[float, float]]) -> Dict[str, float]:
    if not points:
        return {"hpbw": 0.0, "fbr": 0.0, "sll": 0.0, "ripple": 0.0, "max_dir": 0.0}

    gains = [g for _, g in points]
    max_gain = max(gains)
    max_dir = points[gains.index(max_gain)][0]

    # HPBW: ângulos onde ganho > max-3 dB
    upper = lower = max_dir
    for ang, gain in points:
        if gain >= max_gain - 3:
            lower = min(lower, ang)
            upper = max(upper, ang)
    hpbw = upper - lower if upper >= lower else 0.0

    # F/B ratio: diferença entre máximo e valor 180° oposto (aprox)
    back_angle = (max_dir + 180) % 360
    nearest_back = min(points, key=lambda p: abs(p[0] - back_angle))
    fbr = max_gain - nearest_back[1]

    # SLL (side lobes) e ripple simples
    lobe_gains = [g for g in gains if g < max_gain - 3]
    sll = max(lobe_gains) if lobe_gains else 0.0
    ripple = max(gains) - min(gains)

    return {
        "hpbw": round(hpbw, 2),
        "fbr": round(fbr, 2),
        "sll": round(sll, 2),
        "ripple": round(ripple, 2),
        "max_dir": round(max_dir, 2),
        "max_gain": round(max_gain, 2),
    }


def import_pattern(file_storage) -> Dict[str, float]:
    """Recebe um arquivo (werkzeug FileStorage) e devolve métricas."""
    content = file_storage.read().decode("utf-8", errors="ignore")
    data = parse_pattern_csv(content)
    file_storage.seek(0)
    return summarize_pattern(data)
