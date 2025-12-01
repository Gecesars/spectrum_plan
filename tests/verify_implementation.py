import sys
import os
import json
from unittest.mock import MagicMock, patch

# Add the project root to the python path
sys.path.append(os.getcwd())

from app.api.routes_core import dashboard_summary
from app.tasks import run_coverage_simulation
from app.models import Simulation, Station

def test_dashboard_summary():
    print("Testing dashboard_summary endpoint...")
    # Mock session and query results
    with patch("app.api.routes_core.get_session") as mock_get_session:
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        
        # Mock execute results for 3 queries: projects, simulations, artifacts
        mock_session.execute.return_value.scalar_one.side_effect = [10, 5, 20]
        
        # Mock jsonify to return dict for easy checking
        with patch("app.api.routes_core.jsonify", side_effect=lambda x: x):
            response = dashboard_summary()
            
            assert response["total_projects"] == 10
            assert response["total_simulations"] == 5
            assert response["total_artifacts"] == 20
            print("Dashboard summary test PASSED")

def test_run_coverage_simulation_interference():
    print("Testing run_coverage_simulation with interference_deygout...")
    
    # Mock dependencies
    with patch("app.tasks.get_session") as mock_get_session, \
         patch("app.tasks.calculate_coverage") as mock_calc_coverage, \
         patch("app.regulatory.search.find_relevant_neighbors") as mock_find_neighbors, \
         patch("app.regulatory.diffraction.calculate_interference_matrix") as mock_calc_interference:
         
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        
        # Mock simulation object
        mock_simulation = MagicMock(spec=Simulation)
        mock_simulation.calc_type = "interference_deygout"
        mock_simulation.station_id = 1
        mock_simulation.station = MagicMock(spec=Station)
        
        mock_session.get.return_value = mock_simulation
        
        # Mock neighbors found
        mock_neighbor = MagicMock()
        mock_neighbor.station = MagicMock()
        mock_find_neighbors.return_value = [mock_neighbor]
        
        # Mock interference result
        mock_calc_interference.return_value = {
            "heatmap_path": "/path/to/heatmap.png",
            "bbox": {"north": 1, "south": 0, "east": 1, "west": 0}
        }
        
        # Run task
        result = run_coverage_simulation(simulation_id="123", radius_km=10.0)
        
        # Verify interference calculation was called
        mock_calc_interference.assert_called_once()
        assert result["image_path"] == "/path/to/heatmap.png"
        print("Interference simulation test PASSED")

def test_run_coverage_simulation_fallback():
    print("Testing run_coverage_simulation fallback to coverage...")
    
    # Mock dependencies
    with patch("app.tasks.get_session") as mock_get_session, \
         patch("app.tasks.calculate_coverage") as mock_calc_coverage, \
         patch("app.regulatory.search.find_relevant_neighbors") as mock_find_neighbors:
         
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        
        # Mock simulation object
        mock_simulation = MagicMock(spec=Simulation)
        mock_simulation.calc_type = "interference_deygout"
        mock_simulation.station_id = 1
        mock_simulation.station = MagicMock(spec=Station)
        
        mock_session.get.return_value = mock_simulation
        
        # Mock NO neighbors found
        mock_find_neighbors.return_value = []
        
        # Mock coverage result
        mock_calc_coverage.return_value = {
            "image_path": "/path/to/coverage.png",
            "bbox": {"north": 1, "south": 0, "east": 1, "west": 0}
        }
        
        # Run task
        result = run_coverage_simulation(simulation_id="123", radius_km=10.0)
        
        # Verify coverage calculation was called instead
        mock_calc_coverage.assert_called_once()
        assert result["image_path"] == "/path/to/coverage.png"
        print("Fallback simulation test PASSED")

if __name__ == "__main__":
    try:
        test_dashboard_summary()
        test_run_coverage_simulation_interference()
        test_run_coverage_simulation_fallback()
        print("\nAll tests passed successfully!")
    except Exception as e:
        print(f"\nTest failed: {e}")
        exit(1)
