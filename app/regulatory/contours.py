from __future__ import annotations

import math
from typing import List

from app.core.propagation import erp_kw_to_dbm, fspl
from app.models import Station
from app.regulatory.regulatory import RegulatoryStandard
from app.regulatory.search import NeighborCandidate


def protected_field_strength(service_type: str) -> float:
    service = service_type.upper()
    if service == "FM":
        return 66.0
    if service == "TV":
        # Using UHF default; adapt as needed for VHF-High.
        return 48.0
    raise ValueError(f"Unknown service type {service_type}")


def calculate_contour_radius(
    erp_kw: float, antenna_height_m: float, field_strength_dbuv: float, freq_mhz: float
) -> float:
    """Approximate contour radius (km) using an FSPL back-solve as a fast check."""
    prx_dbm = field_strength_dbuv - 20 * math.log10(freq_mhz) - 77.2
    tx_dbm = erp_kw_to_dbm(erp_kw)
    path_loss = tx_dbm - prx_dbm
    # Invert FSPL to distance.
    distance_km = 10 ** ((path_loss - 32.44 - 20 * math.log10(freq_mhz)) / 20)
    # Bias by antenna height: more height, slightly larger reach.
    return max(distance_km * (1 + antenna_height_m / 1000.0), 0.1)


def analyze_contours(
    proposal: Station,
    neighbors: List[NeighborCandidate],
    standard: RegulatoryStandard,
) -> List[dict]:
    """Flag neighbors where Rp + Ri exceeds separation (fail-fast filter)."""
    e_min = protected_field_strength(proposal.service_type)
    rp = calculate_contour_radius(proposal.erp_kw, proposal.antenna_height_m, e_min, proposal.frequency_mhz)

    critical = []
    for neighbor in neighbors:
        offset = _freq_offset(proposal, neighbor.station)
        pr = standard.get_required_pr(proposal.service_type, offset)
        e_int = e_min - pr
        ri = calculate_contour_radius(
            neighbor.station.erp_kw,
            neighbor.station.antenna_height_m,
            e_int,
            neighbor.station.frequency_mhz,
        )
        if neighbor.distance_km < (rp + ri):
            critical.append(
                {
                    "neighbor_station_id": neighbor.station.id,
                    "distance_km": neighbor.distance_km,
                    "azimuth_deg": neighbor.azimuth_deg,
                    "protection_ratio_db": pr,
                    "protected_radius_km": rp,
                    "interferer_radius_km": ri,
                }
            )
    return critical


def _freq_offset(proposal: Station, neighbor: Station) -> float:
    if proposal.service_type.upper() == "FM":
        return (neighbor.frequency_mhz - proposal.frequency_mhz) * 1000.0  # kHz
    return neighbor.frequency_mhz - proposal.frequency_mhz  # MHz channel spacing
