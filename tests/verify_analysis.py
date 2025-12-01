import sys
import os
import traceback
from unittest.mock import MagicMock, patch
from flask import Flask

# Add the project root to the python path
sys.path.append(os.getcwd())

from app import create_app
from app.config import Config
from app.models import User, Project, AntennaModel

class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    LOGIN_DISABLED = True # Disable login_required

def test_analysis_page():
    print("Testing Analysis Page...")
    
    app = create_app(TestConfig)
    
    with app.test_client() as client:
        # Mock get_session
        with patch("app.api.routes_analysis.get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value.__enter__.return_value = mock_session
            
            # Patch current_user to simulate logged in user
            with patch("app.api.routes_analysis.current_user") as mock_current_user:
                mock_current_user.id = 1
                mock_current_user.is_authenticated = True
                
                # 1. Test GET /analysis/new
                print("1. Testing GET /analysis/new...")
                mock_session.execute.return_value.scalars.return_value.all.return_value = [] 
                
                try:
                    response = client.get("/analysis/new")
                    print(f"   Status: {response.status_code}")
                    if response.status_code != 200:
                        print(f"   Data: {response.data}")
                    
                    assert response.status_code == 200
                    assert b"Nova An" in response.data
                    print("   GET PASSED")
                except Exception:
                    traceback.print_exc()
                    raise
                
                # 2. Test POST /analysis/new
                print("2. Testing POST /analysis/new...")
                
                mock_project = MagicMock(spec=Project)
                mock_project.id = 10
                
                try:
                    response = client.post("/analysis/new", data={
                        "new_project_name": "Test Project",
                        "station_name": "Test Station",
                        "latitude": "-23.5",
                        "longitude": "-46.6",
                        "site_elevation": "760",
                        "frequency_mhz": "98.5",
                        "erp_kw": "5",
                        "antenna_height": "50",
                        "service_class": "A",
                        "station_type": "FM",
                        "azimuth": "0",
                        "mechanical_tilt": "0",
                        "polarization": "Vertical"
                    }, follow_redirects=True)
                    
                    print(f"   Status: {response.status_code}")
                    
                    assert response.status_code == 200
                    assert mock_session.add.call_count >= 2 
                    print("   POST PASSED")
                except Exception:
                    traceback.print_exc()
                    raise

                # 3. Test GET /analysis/antenna-pattern/<id>
                print("3. Testing Antenna Pattern API...")
                mock_model = MagicMock(spec=AntennaModel)
                mock_model.name = "Test Antenna"
                mock_model.horizontal_pattern = {"0": 0, "90": 10}
                mock_model.vertical_pattern = {"0": 0, "90": 10}
                mock_model.gain_dbi = 10.0
                
                mock_session.get.return_value = mock_model
                
                try:
                    response = client.get("/analysis/antenna-pattern/1")
                    assert response.status_code == 200
                    assert response.json["name"] == "Test Antenna"
                    print("   Antenna API PASSED")
                except Exception:
                    traceback.print_exc()
                    raise

if __name__ == "__main__":
    try:
        test_analysis_page()
        print("\nAll analysis tests passed successfully!")
    except Exception as e:
        print(f"\nTest failed: {e}")
        exit(1)
