from unittest.mock import MagicMock
from app.core.engine.diffraction import DeygoutMatrix
from app.models import Station

def test_deygout_matrix_structure():
    provider = MagicMock()
    # Mock elevation profile: Flat terrain
    # get_elevation_profile takes lists, returns list of ints
    provider.get_elevation_profile.return_value = [100, 100, 100, 100, 100]
    
    matrix = DeygoutMatrix(provider)
    
    proposal = Station(
        id=1, station_type="FM", frequency_mhz=100.0, 
        erp_kw=1.0, antenna_height=150.0,
        latitude=0.0, longitude=0.0
    )
    
    interferer = Station(
        id=2, station_type="FM", frequency_mhz=100.0, 
        erp_kw=1.0, antenna_height=150.0,
        latitude=0.0, longitude=0.1 # Close
    )
    
    # Grid res large to reduce iterations
    result = matrix.calculate_matrix(proposal, interferer, grid_res_km=5.0)
    
    assert "impacted_area_km2" in result
    assert result["impacted_area_km2"] >= 0.0
