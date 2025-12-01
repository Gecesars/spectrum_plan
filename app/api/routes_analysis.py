from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import select

from app.config import get_session
from app.models import Project, Station, AntennaModel, User

analysis_bp = Blueprint("analysis", __name__)

@analysis_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_analysis():
    if request.method == "POST":
        # Handle form submission
        data = request.form
        
        project_id = data.get("project_id")
        new_project_name = data.get("new_project_name")
        
        with get_session() as session:
            # Project handling
            if new_project_name:
                project = Project(name=new_project_name, user_id=current_user.id)
                session.add(project)
                session.flush() # Get ID
            elif project_id:
                project = session.get(Project, int(project_id))
                if not project:
                    flash("Project not found", "error")
                    return redirect(url_for("analysis.new_analysis"))
            else:
                flash("Please select or create a project", "error")
                return redirect(url_for("analysis.new_analysis"))
            
            # Station creation
            try:
                station = Station(
                    project_id=project.id,
                    name=data.get("station_name"),
                    latitude=float(data.get("latitude")),
                    longitude=float(data.get("longitude")),
                    site_elevation=float(data.get("site_elevation", 0)),
                    frequency_mhz=float(data.get("frequency_mhz")),
                    erp_kw=float(data.get("erp_kw")),
                    antenna_height=float(data.get("antenna_height")),
                    service_class=data.get("service_class"),
                    station_type=data.get("station_type", "FM"),
                    antenna_model_id=int(data.get("antenna_model_id")) if data.get("antenna_model_id") else None,
                    azimuth=float(data.get("azimuth", 0)),
                    mechanical_tilt=float(data.get("mechanical_tilt", 0)),
                    polarization=data.get("polarization", "Vertical")
                )
                session.add(station)
                session.commit()
                flash("Station created successfully!", "success")
                return redirect(url_for("web.index")) # Redirect to dashboard or simulation page
            except ValueError as e:
                flash(f"Invalid input: {e}", "error")
                return redirect(url_for("analysis.new_analysis"))

    # GET request: Render form
    with get_session() as session:
        projects = session.execute(select(Project).where(Project.user_id == current_user.id)).scalars().all()
        antenna_models = session.execute(select(AntennaModel)).scalars().all()
        
    return render_template(
        "analysis/new.html",
        projects=projects,
        antenna_models=antenna_models
    )

@analysis_bp.route("/antenna-pattern/<int:model_id>")
@login_required
def get_antenna_pattern(model_id):
    with get_session() as session:
        model = session.get(AntennaModel, model_id)
        if not model:
            return jsonify({"error": "Model not found"}), 404
        
        return jsonify({
            "name": model.name,
            "horizontal_pattern": model.horizontal_pattern,
            "vertical_pattern": model.vertical_pattern,
            "gain_dbi": model.gain_dbi
        })
