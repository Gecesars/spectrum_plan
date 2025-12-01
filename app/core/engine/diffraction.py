from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Dict

import numpy as np
from geopy.distance import geodesic

from app.models import Station
from app.core.terrain import ElevationProvider
from app.core.propagation import fspl
from app.core.engine.protection import RegulatoryStandard


@dataclass
class InterferencePoint:
    lat: float
    lon: float
    margin_db: float
    is_interference: bool


class DeygoutMatrix:
    def __init__(self, elevation_provider: ElevationProvider):
        self.provider = elevation_provider

    def calculate_matrix(
        self,
        proposal: Station,
        interferer: Station,
        grid_res_km: float = 1.0
    ) -> Dict[str, float]:
        """
        Calculates interference matrix using Deygout diffraction.
        Returns summary stats.
        """
        # 1. Define Grid (Bounding Box of Proposal's Protected Contour)
        # For simplicity, we use a fixed box around proposal, e.g. 20km
        # In real app, this comes from ContourAnalysis Rp.
        radius_km = 20.0 
        
        lat_min = proposal.latitude - (radius_km / 111.0)
        lat_max = proposal.latitude + (radius_km / 111.0)
        lon_min = proposal.longitude - (radius_km / 111.0)
        lon_max = proposal.longitude + (radius_km / 111.0)
        
        lats = np.arange(lat_min, lat_max, grid_res_km / 111.0)
        lons = np.arange(lon_min, lon_max, grid_res_km / 111.0)
        
        impacted_points = 0
        total_points = 0
        
        # Protection Ratio
        if proposal.station_type == "FM":
            offset = abs(proposal.frequency_mhz - interferer.frequency_mhz) * 1000.0
        else:
            # TV
            if proposal.channel_number and interferer.channel_number:
                offset = float(abs(proposal.channel_number - interferer.channel_number))
            else:
                offset = abs(proposal.frequency_mhz - interferer.frequency_mhz) / 6.0
             
        pr = RegulatoryStandard.get_required_pr(proposal.station_type, offset)
        if pr == -999.0:
            return {"impacted_area_km2": 0.0, "max_margin": 999.0}

        for lat in lats:
            for lon in lons:
                total_points += 1
                
                # Signal Wanted (Proposal -> Point)
                s_wanted = self._calculate_signal(proposal, lat, lon)
                
                # Signal Unwanted (Interferer -> Point)
                s_unwanted = self._calculate_signal(interferer, lat, lon)
                
                margin = s_wanted - s_unwanted
                
                if margin < pr:
                    impacted_points += 1
                    
        area = impacted_points * (grid_res_km ** 2)
        return {"impacted_area_km2": area}

    def _calculate_signal(self, station: Station, lat: float, lon: float) -> float:
        dist_km = geodesic((station.latitude, station.longitude), (lat, lon)).km
        if dist_km < 0.1:
            dist_km = 0.1
            
        # Free Space Loss
        loss_fs = fspl(dist_km, station.frequency_mhz)
        
        # Diffraction Loss (Deygout)
        loss_diff = 0.0
        try:
            # We need discrete points. 
            num_points = int(dist_km * 10)  # 100m res
            if num_points < 5:
                num_points = 5
                
            lats = np.linspace(station.latitude, lat, num_points)
            lons = np.linspace(station.longitude, lon, num_points)
            
            elevs = self.provider.get_elevation_profile(lats, lons)
            
            # Calculate diffraction
            loss_diff = self._deygout_loss(elevs, dist_km, station.frequency_mhz, station.antenna_height, 10.0)  # Rx height 10m
        except Exception:
            # Fallback if SRTM fails or other error
            loss_diff = 0.0
            
        total_loss = loss_fs + loss_diff
        
        # E = 106.9 - Loss + ERP_dBk
        erp_dbk = 10 * math.log10(max(station.erp_kw, 0.001))
        e_field = 106.9 - total_loss + erp_dbk
        
        return e_field

    def _deygout_loss(self, elevations: List[int], dist_km: float, freq_mhz: float, h_tx: float, h_rx: float) -> float:
        """
        Simplified Knife-Edge Diffraction (Single Peak) for now.
        Full Deygout is recursive.
        """
        # Find max obstruction
        n = len(elevations)
        if n < 3:
            return 0.0
            
        # Line of Sight
        d_step = dist_km / (n - 1)
        
        max_v = -999.0
        
        for i in range(1, n - 1):
            d1 = i * d_step
            d2 = dist_km - d1
            
            # LoS height at i (Earth curvature ignored for simplicity in this step, 
            # but usually h_eff = h + d1*d2/12.75)
            # Let's add simple earth curvature correction to h_obs
            
            # Earth bulge (m) ~= d1(km) * d2(km) * 0.0785 (for k=4/3)
            # or d^2 / 2R. 
            # Let's use standard parabolic approx: h = d1*d2 / (2 * k * R)
            # k=4/3, R=6371 -> 2*1.33*6371 ~= 17000
            # h_bulge = (d1*d2 * 1000^2) / 17000000 ... wait.
            # Standard approx: h(m) = 0.078 * d1(km) * d2(km)
            
            h_bulge = 0.078 * d1 * d2
            
            h_los = h_tx + (h_rx - h_tx) * (d1 / dist_km)
            
            # Obstacle height (terrain + bulge)
            h_obs = elevations[i] + h_bulge
            
            # Clearance
            h = h_obs - h_los  # Positive if blocking
            
            # Fresnel parameter v
            wavelength = 300.0 / freq_mhz  # meters
            
            # d1, d2 in km -> convert to m
            # v = h * sqrt( (2/lambda) * (1/d1 + 1/d2) )
            if d1 > 0 and d2 > 0:
                v = h * math.sqrt((2.0 / wavelength) * (1.0 / (d1 * 1000) + 1.0 / (d2 * 1000)))
                if v > max_v:
                    max_v = v
                
        # Diffraction Loss J(v)
        if max_v <= -0.7:
            return 0.0
        elif max_v <= 0:
            return 0.0  # Simplification: no loss if clearance > 0.6 Fresnel
        else:
            # Lee approximation for knife edge
            return 6.9 + 20 * math.log10(math.sqrt((max_v - 0.1)**2 + 1) + max_v - 0.1)
