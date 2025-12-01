from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

from geopy.distance import geodesic
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Station


@dataclass
class NeighborCandidate:
    station: Station
    distance_km: float
    azimuth_deg: float


class NeighborDiscovery:
    def __init__(self, session: Session):
        self.session = session

    def find_neighbors(
        self,
        proposal: Station,
        service_type: str = "FM",
        limit: int = 100
    ) -> List[NeighborCandidate]:
        """
        Find relevant stations that could interact with the proposal.
        
        Filters:
        1. Spatial: 300km (FM) or 400km (TV)
        2. Spectral: +/- 600kHz (FM) or +/- 1 Channel (TV)
        """
        # 1. Define Spatial Radius
        if service_type.upper() == "TV":
            radius_km = 400.0
            # TV Channel bandwidth approx 6MHz. 
            # We assume +/- 1 channel = +/- 6 MHz.
            freq_margin = 6.0 
        else:
            radius_km = 300.0
            # FM: +/- 600 kHz = 0.6 MHz
            freq_margin = 0.6
            
        min_freq = proposal.frequency_mhz - freq_margin
        max_freq = proposal.frequency_mhz + freq_margin

        # 2. Query with Spatial & Spectral filters
        # We cast Geometry to Geography to use meters in ST_DWithin
        # Station.location is assumed to be SRID 4326
        
        proposal_geom = func.ST_SetSRID(
            func.ST_MakePoint(proposal.longitude, proposal.latitude), 
            4326
        )
        
        query = self.session.query(Station).filter(
            Station.station_type == service_type,
            Station.frequency_mhz >= min_freq,
            Station.frequency_mhz <= max_freq,
            func.ST_DWithin(
                Station.location.cast(func.Geography),
                proposal_geom.cast(func.Geography),
                radius_km * 1000  # meters
            )
        )
        
        # Exclude self if existing
        if proposal.id:
            query = query.filter(Station.id != proposal.id)
            
        candidates = []
        # Fetch results
        neighbors = query.limit(limit).all()
        
        for station in neighbors:
            # Calculate precise distance and azimuth
            p1 = (proposal.latitude, proposal.longitude)
            p2 = (station.latitude, station.longitude)
            dist = geodesic(p1, p2).km
            
            azimuth = self._calculate_azimuth(
                proposal.latitude, proposal.longitude,
                station.latitude, station.longitude
            )
            
            candidates.append(NeighborCandidate(station, dist, azimuth))
            
        return candidates

    def _calculate_azimuth(self, lat1, lon1, lat2, lon2) -> float:
        """
        Calculate initial bearing (azimuth) from point A to point B.
        """
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        d_lon = math.radians(lon2 - lon1)

        y = math.sin(d_lon) * math.cos(lat2_rad)
        x = math.cos(lat1_rad) * math.sin(lat2_rad) - \
            math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(d_lon)
            
        bearing_rad = math.atan2(y, x)
        bearing_deg = math.degrees(bearing_rad)
        
        return (bearing_deg + 360) % 360
