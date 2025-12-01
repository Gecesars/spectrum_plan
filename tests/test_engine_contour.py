from app.core.engine.contour import ContourAnalysis
from app.core.engine.discovery import NeighborCandidate
from app.models import Station

def test_contour_analysis_fail_fail():
    # Proposal: 1kW, 150m, 100MHz (FM)
    proposal = Station(
        id=1, station_type="FM", frequency_mhz=100.0, 
        erp_kw=1.0, antenna_height=150.0,
        latitude=0.0, longitude=0.0
    )
    
    # Neighbor: 1kW, 150m, 100MHz (Co-channel) -> PR=45dB
    # E_min = 66
    # E_int = 66 - 45 = 21 dBuV/m
    # Rp + Ri should be large.
    
    neighbor_st = Station(
        id=2, station_type="FM", frequency_mhz=100.0, 
        erp_kw=1.0, antenna_height=150.0,
        latitude=0.0, longitude=1.0 
    )
    
    # Distance 100km (approx)
    cand = NeighborCandidate(neighbor_st, 100.0, 90.0)
    
    analysis = ContourAnalysis()
    critical = analysis.analyze_contours(proposal, [cand])
    
    # Should be critical
    assert len(critical) == 1
    assert critical[0].station.id == 2
    assert critical[0].margin_db < 0 # Overlap

def test_contour_analysis_safe():
    # Same setup but distance 1000km
    proposal = Station(
        id=1, station_type="FM", frequency_mhz=100.0, 
        erp_kw=1.0, antenna_height=150.0,
        latitude=0.0, longitude=0.0
    )
    neighbor_st = Station(
        id=2, station_type="FM", frequency_mhz=100.0, 
        erp_kw=1.0, antenna_height=150.0,
        latitude=0.0, longitude=10.0 
    )
    cand = NeighborCandidate(neighbor_st, 1000.0, 90.0)
    
    analysis = ContourAnalysis()
    critical = analysis.analyze_contours(proposal, [cand])
    
    assert len(critical) == 0
