from __future__ import annotations

from typing import Any, Dict


def summarize_coverage(payload: Dict[str, Any]) -> Dict[str, Any]:
    last = (payload or {}).get('lastCoverage') or {}
    if not last:
        return {}
    return {
        'engine': last.get('engine'),
        'radius_km': last.get('radius_km') or last.get('requested_radius_km'),
        'center_metrics': last.get('center_metrics'),
        'loss_components': last.get('loss_components'),
        'generated_at': last.get('generated_at'),
    }
