from __future__ import annotations

import math


def estimate_power_density(erp_kw: float, distance_m: float) -> float:
    distance_m = max(distance_m, 0.5)
    erp_w = max(erp_kw, 0.0) * 1000
    return erp_w / (4 * math.pi * distance_m ** 2)


def limit_for_environment(environment: str, frequency_mhz: float) -> float:
    environment = (environment or 'ocupacional').lower()
    if environment.startswith('pub'):
        base = 4.5e-3  # público geral
    else:
        base = 2e-2  # ocupacional
    if frequency_mhz and frequency_mhz > 300:
        base *= 2
    return base
