from __future__ import annotations

import math
from typing import Dict, Any


def compute_erp_data(system: Dict[str, Any]) -> Dict[str, float]:
    power_w = float(system.get('potencia_w', system.get('potencia')) or 1.0)
    tx_gain = float(system.get('ganho_tx_dbi', system.get('ganho_dbi')) or 0.0)
    losses = float(system.get('perdas_db', system.get('perdas')) or 0.0)

    power_dbm = 10 * math.log10(max(power_w, 1e-6) * 1000)
    eirp_dbm = power_dbm + tx_gain - losses
    eirp_dbw = eirp_dbm - 30
    erp_w = 10 ** ((eirp_dbm - 36.2) / 10)  # aprox.
    erp_kw = erp_w / 1000

    return {
        "power_w": power_w,
        "eirp_dbm": round(eirp_dbm, 2),
        "eirp_dbw": round(eirp_dbw, 2),
        "erp_kw": round(erp_kw, 3),
        "losses_db": losses,
    }
