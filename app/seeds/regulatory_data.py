from app.config import get_session
from app.models import RegulatoryClass
from sqlalchemy import select

def seed_regulatory_data():
    """Seeds the database with FM regulatory classes."""
    
    # Data from Table 2 of requirements
    fm_classes = [
        {"class_name": "E1", "max_erp_kw": 100, "max_erp_dbk": 20.0, "dist_km": 78.5, "height_m": 600},
        {"class_name": "E2", "max_erp_kw": 75, "max_erp_dbk": 18.8, "dist_km": 67.5, "height_m": 450},
        {"class_name": "E3", "max_erp_kw": 60, "max_erp_dbk": 17.8, "dist_km": 54.5, "height_m": 300},
        {"class_name": "A1", "max_erp_kw": 50, "max_erp_dbk": 17.0, "dist_km": 38.5, "height_m": 150},
        {"class_name": "A2", "max_erp_kw": 30, "max_erp_dbk": 14.8, "dist_km": 35.0, "height_m": 150},
        {"class_name": "A3", "max_erp_kw": 15, "max_erp_dbk": 11.8, "dist_km": 30.0, "height_m": 150},
        {"class_name": "A4", "max_erp_kw": 5, "max_erp_dbk": 7.0, "dist_km": 24.0, "height_m": 150},
        {"class_name": "B1", "max_erp_kw": 3, "max_erp_dbk": 4.8, "dist_km": 16.5, "height_m": 90},
        {"class_name": "B2", "max_erp_kw": 1, "max_erp_dbk": 0.0, "dist_km": 12.5, "height_m": 90},
        {"class_name": "C", "max_erp_kw": 0.3, "max_erp_dbk": -5.2, "dist_km": 7.5, "height_m": 60},
    ]

    with get_session() as session:
        print("Seeding Regulatory Classes...")
        for data in fm_classes:
            # Check if exists
            stmt = select(RegulatoryClass).where(
                RegulatoryClass.service_type == "FM",
                RegulatoryClass.class_name == data["class_name"]
            )
            existing = session.execute(stmt).scalar_one_or_none()
            
            if not existing:
                reg_class = RegulatoryClass(
                    service_type="FM",
                    class_name=data["class_name"],
                    max_erp_kw=data["max_erp_kw"],
                    max_erp_dbk=data["max_erp_dbk"],
                    protected_contour_distance_km=data["dist_km"],
                    reference_height_m=data["height_m"],
                    protected_contour_level_dbuv=66.0 # Standard for FM
                )
                session.add(reg_class)
                print(f"Added Class {data['class_name']}")
            else:
                # Update if needed (optional, here we just skip)
                print(f"Class {data['class_name']} already exists.")
        
        session.commit()
        print("Seeding complete.")
