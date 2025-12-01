from unittest.mock import MagicMock
from app.core.engine.discovery import NeighborDiscovery
from app.models import Station

def test_find_neighbors_fm():
    session = MagicMock()
    # Mock query chain: session.query(Station).filter(...).filter(...).limit(...).all()
    # Note: discovery.py calls query(Station).filter(...) then if proposal.id, filter again.
    
    # We need to mock the return values of the chain
    q1 = session.query.return_value
    q2 = q1.filter.return_value
    q3 = q2.filter.return_value # For the second filter (id != proposal.id)
    
    # The final call is .limit(limit).all()
    # Depending on how many filters are chained, we might need more mocks or a recursive mock.
    # MagicMock handles chaining by default, but we need to set the return value of the FINAL call.
    
    # Let's just set the return value of .all() on the end of the chain.
    # Since we don't know exactly how many calls, we can set it on the mock object itself if we are careful,
    # or just assume the chain structure.
    
    # A robust way with MagicMock:
    # session.query().filter()...limit().all.return_value = [...]
    
    s1 = Station(id=2, latitude=-23.5, longitude=-46.6, frequency_mhz=100.1, station_type="FM")
    
    # We can just mock the final result of the chain
    session.query.return_value.filter.return_value.filter.return_value.limit.return_value.all.return_value = [s1]
    # Also handle the case where only one filter is called (if proposal.id is None)
    session.query.return_value.filter.return_value.limit.return_value.all.return_value = [s1]

    discovery = NeighborDiscovery(session)
    proposal = Station(id=1, latitude=-23.6, longitude=-46.7, frequency_mhz=100.1, station_type="FM")
    
    candidates = discovery.find_neighbors(proposal, "FM")
    
    assert len(candidates) == 1
    assert candidates[0].station.id == 2
    assert candidates[0].distance_km > 0
    # Check azimuth is calculated
    assert candidates[0].azimuth_deg >= 0
