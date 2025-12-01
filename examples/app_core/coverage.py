
from flask import current_app
from .models import db, CoverageJob, Asset, CoverageStatus
import rasterio
from rasterio.windows import Window
import numpy as np
from geopy.distance import geodesic
from geopy.point import Point
import math
from . import p1546

def run_p1546_coverage(job: CoverageJob):
    """
    Runs a coverage analysis using the ITU-R P.1546 model.
    """
    current_app.logger.info(f"Running P.1546 coverage job {job.id} for project {job.project_id}")

    try:
        # Mark job as running
        job.status = CoverageStatus.running
        db.session.commit()

        # Get inputs from job.inputs
        inputs = job.inputs
        f = inputs.get('frequency')
        t = inputs.get('time_percentage', 50)
        h2 = inputs.get('receiver_height', 1.5)
        R2 = inputs.get('clutter_height', 10)
        area = inputs.get('area_type', 'Rural')
        pathinfo = inputs.get('pathinfo', 1)
        tx_lat = inputs.get('tx_lat')
        tx_lon = inputs.get('tx_lon')
        rx_lat = inputs.get('rx_lat')
        rx_lon = inputs.get('rx_lon')
        antenna_height_agl = inputs.get('antenna_height_agl')

        # Get DEM and LULC assets
        dem_asset = Asset.query.filter_by(project_id=job.project_id, type='dem').first()
        lulc_asset = Asset.query.filter_by(project_id=job.project_id, type='lulc').first()

        if not dem_asset or not lulc_asset:
            raise Exception("DEM and LULC assets not found for this project.")

        # Calculate effective antenna height (h1)
        heff = calculate_effective_antenna_height(tx_lat, tx_lon, rx_lat, rx_lon, dem_asset, antenna_height_agl)

        # Determine path type
        path_type = get_path_type(tx_lat, tx_lon, rx_lat, rx_lon, lulc_asset)

        # Get distance
        distance_km = geodesic(Point(tx_lat, tx_lon), Point(rx_lat, rx_lon)).km

        # Run the P.1546 calculation
        E, L = p1546.bt_loss(f, t, heff, h2, R2, area, [distance_km], [path_type], pathinfo)

        # TODO: Generate output artifacts (GeoTIFF, PNG, etc.)
        # TODO: Create Asset records for the outputs

        job.status = CoverageStatus.succeeded
        job.metrics = {"field_strength": E, "transmission_loss": L}
        db.session.commit()

        current_app.logger.info(f"P.1546 coverage job {job.id} completed successfully.")

    except Exception as e:
        current_app.logger.error(f"P.1546 coverage job {job.id} failed: {e}")
        job.status = CoverageStatus.failed
        job.metrics = {"error": str(e)}
        db.session.commit()
