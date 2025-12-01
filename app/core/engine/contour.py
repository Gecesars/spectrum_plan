from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

from app.models import Station
from app.core.engine.protection import RegulatoryStandard
from app.core.engine.discovery import NeighborCandidate


@dataclass
class CriticalNeighbor:
    station: Station
    margin_db: float  # Negative means interference potential (overlap)
    distance_km: float


class ContourAnalysis:
    
    # E_min constants (dBuV/m)
    EMIN_FM_URBAN = 66.0
    EMIN_TV_UHF = 48.0
    EMIN_TV_VHF_HIGH = 51.0
    
    def analyze_contours(
        self, 
        proposal: Station, 
        neighbors: List[NeighborCandidate]
    ) -> List[CriticalNeighbor]:
        """
        Filters neighbors using the Fail-Fail (Rp + Ri) method.
        """
        critical = []
        
        # 1. Determine E_min for Proposal
        e_min = self._get_emin(proposal)
        
        # 2. Calculate Rp (Protected Radius) for Proposal
        # 50% Time, 50% Loc
        rp_km = self._calculate_p1546_distance(
            proposal.erp_kw, proposal.antenna_height, proposal.frequency_mhz, 
            field_strength_target=e_min, time_pct=50
        )
        
        for cand in neighbors:
            neighbor = cand.station
            
            # 3. Determine Protection Ratio
            # Offset calculation
            if proposal.station_type == "FM":
                offset = abs(proposal.frequency_mhz - neighbor.frequency_mhz) * 1000.0  # kHz
            else:
                # TV: Channel diff
                if proposal.channel_number and neighbor.channel_number:
                    offset = float(abs(proposal.channel_number - neighbor.channel_number))
                else:
                    # Fallback: estimate channel diff (6MHz bw)
                    offset = abs(proposal.frequency_mhz - neighbor.frequency_mhz) / 6.0
            
            pr = RegulatoryStandard.get_required_pr(proposal.station_type, offset)
            
            if pr == -999.0:
                continue  # No interference possible (unregulated offset)
                
            # 4. Calculate E_int
            e_int = e_min - pr
            
            # 5. Calculate Ri (Interfering Radius) for Neighbor
            # 1% Time (Interference is rare but bad), 50% Loc
            ri_km = self._calculate_p1546_distance(
                neighbor.erp_kw, neighbor.antenna_height, neighbor.frequency_mhz,
                field_strength_target=e_int, time_pct=1
            )
            
            # 6. Check Distance
            dist_km = cand.distance_km
            
            # If Distance < Rp + Ri, there is a theoretical overlap
            if dist_km < (rp_km + ri_km):
                # We store the overlap as a negative margin proxy
                overlap = (rp_km + ri_km) - dist_km
                critical.append(CriticalNeighbor(neighbor, -overlap, dist_km))
                
        return critical

    def _get_emin(self, station: Station) -> float:
        if station.station_type == "FM":
            return self.EMIN_FM_URBAN
        elif station.station_type == "TV":
            if station.frequency_mhz > 300:  # UHF
                return self.EMIN_TV_UHF
            else:
                return self.EMIN_TV_VHF_HIGH
        return 60.0  # Default

    def _calculate_p1546_distance(
        self, erp_kw: float, h_tx: float, freq_mhz: float, 
        field_strength_target: float, time_pct: int
    ) -> float:
        """
        Inverse P.1546: Find distance where Field Strength == Target.
        
        Approximation used:
        E = Base + P_gain + H_gain + T_gain - k * log10(d)
        
        Where:
        - Base: ~100 dBuV/m @ 1km, 1kW, 150m
        - k: Propagation slope (35 for interference/far field)
        """
        # ERP dBk (relative to 1kW)
        erp_dbk = 10 * math.log10(max(erp_kw, 0.001))
        
        base_e = 100.0  # dBuV/m at 1km, 1kW, 150m
        
        # Height gain: 20 log(h / 150)
        h_gain = 20 * math.log10(max(h_tx, 10.0) / 150.0)
        
        p_gain = erp_dbk
        
        # Time correction (1% time enhances signal by ~10-12dB in P.1546 curves)
        t_gain = 0.0
        if time_pct == 1:
            t_gain = 12.0
            
        # Propagation slope k
        # Free space is 20. P.1546 land path is steeper, ~30-40.
        k = 35.0
        
        constant = base_e + h_gain + p_gain + t_gain
        
        # log10(d) = (Constant - Target) / k
        if constant < field_strength_target:
            return 0.1  # Target is stronger than max possible at 1km
            
        log_d = (constant - field_strength_target) / k
        d_km = 10 ** log_d
        
        return d_km
