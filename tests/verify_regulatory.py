import sys
import os
from unittest.mock import MagicMock, patch
from flask import Flask

# Add the project root to the python path
sys.path.append(os.getcwd())

from app import create_app
from app.config import Config
from app.models import RegulatoryClass
from sqlalchemy import select

class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False

def test_regulatory_data():
    print("Testing Regulatory Data...")
    
    app = create_app(TestConfig)
    
    with app.app_context():
        # Run seeding
        from app.seeds.regulatory_data import seed_regulatory_data
        seed_regulatory_data()
        
        # Verify E1
        from app.config import get_session
        with get_session() as session:
            e1 = session.execute(select(RegulatoryClass).where(RegulatoryClass.class_name == "E1")).scalar_one_or_none()
            assert e1 is not None
            assert e1.max_erp_kw == 100
            assert e1.protected_contour_distance_km == 78.5
            print("   Class E1 Verified")
            
            # Verify C
            c = session.execute(select(RegulatoryClass).where(RegulatoryClass.class_name == "C")).scalar_one_or_none()
            assert c is not None
            assert c.max_erp_kw == 0.3
            assert c.protected_contour_distance_km == 7.5
            print("   Class C Verified")

if __name__ == "__main__":
    try:
        test_regulatory_data()
        print("\nAll regulatory tests passed successfully!")
    except Exception as e:
        print(f"\nTest failed: {e}")
        exit(1)
