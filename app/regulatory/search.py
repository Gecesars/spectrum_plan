from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

from geoalchemy2.shape import to_shape
from pyproj import Geod
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import Station

geod = Geod(ellps="WGS84")


@dataclass
class NeighborCandidate:
    station: Station
    distance_km: float
    azimuth_deg: float


def _radius_for_service(service_type: str) -> float:
    return 300.0 if service_type.upper() == "FM" else 400.0


def _frequency_window(station: Station) -> tuple[float, float]:
    if station.service_type.upper() == "FM":
        return station.frequency_mhz - 0.6, station.frequency_mhz + 0.6
    # TV channels are 6 MHz wide; include +/- 1 channel
    return station.frequency_mhz - 6.0, station.frequency_mhz + 6.0


def find_relevant_neighbors(proposal: Station, session: Session) -> List[NeighborCandidate]:
    """Return neighbors within regulatory distance and adjacency mask."""
    radius_km = _radius_for_service(proposal.service_type)
    freq_min, freq_max = _frequency_window(proposal)
    proposal_shape = to_shape(proposal.location)

    stmt = (
        select(Station)
        .where(
            and_(
                Station.id != proposal.id,
                Station.service_type == proposal.service_type,
                Station.frequency_mhz.between(freq_min, freq_max),
                # Cast to geography to leverage geodesic distance inside PostGIS.
                # ST_DWithin accepts meters on geography.
                func.ST_DWithin(
                    func.Geography(Station.location),
                    func.Geography(proposal.location),
                    radius_km * 1000.0,
                ),
            )
        )
    )
    neighbors = []
    for station in session.execute(stmt).scalars().all():
        shape = to_shape(station.location)
        az12, _, dist_m = geod.inv(proposal_shape.x, proposal_shape.y, shape.x, shape.y)
        neighbors.append(
            NeighborCandidate(
                station=station,
                distance_km=dist_m / 1000.0,
                azimuth_deg=(az12 + 360.0) % 360.0,
            )
        )
    return neighbors
