from __future__ import annotations

import math
from typing import Any, Dict, List


LIGHT_SPEED = 299792458  # m/s


def fspl(distance_km: float, frequency_mhz: float) -> float:
    distance_m = max(distance_km, 0.001) * 1000
    frequency_hz = frequency_mhz * 1e6
    return 20 * math.log10(distance_m) + 20 * math.log10(frequency_hz) - 147.55


def evaluate_links(links: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {"links": [], "links_sem_margem": []}
    for link in links:
        freq = float(link.get('frequencia_mhz') or 6000)
        dist = float(link.get('distancia_km') or 10)
        tx_power = float(link.get('potencia_dbm') or 30)
        tx_gain = float(link.get('ganho_tx_dbi') or 0)
        rx_gain = float(link.get('ganho_rx_dbi') or 0)
        losses = float(link.get('perdas_db') or 0)

        path_loss = fspl(dist, freq) + losses
        received = tx_power + tx_gain + rx_gain - path_loss
        sensitivity = float(link.get('sensibilidade_dbm') or -95)
        margin = received - sensitivity

        summary['links'].append({
            'identificacao': link.get('identificacao', 'Link'),
            'fspl_db': round(path_loss, 2),
            'rx_dbm': round(received, 2),
            'margin_db': round(margin, 2),
        })
        if margin < 3:
            summary['links_sem_margem'].append(link.get('identificacao', 'Link'))
    return summary
