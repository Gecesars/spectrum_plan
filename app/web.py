from __future__ import annotations

from flask import Blueprint, render_template, redirect, url_for
from flask_login import current_user, login_required

from app.config import get_session
from app.models import Project

web_bp = Blueprint("web", __name__)


@web_bp.get("/")
def index():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login_get"))
    return render_template("home.html")


@web_bp.get("/database")
@login_required
def database():
    return render_template("database.html")


@web_bp.get("/network")
@login_required
def network():
    return render_template("network.html")


@web_bp.get("/map")
@login_required
def map_view():
    return render_template("map.html")


@web_bp.get("/projects")
@login_required
def projects_view():
    with get_session() as session:
        projects = session.query(Project).filter(Project.user_id == current_user.id).all()
    return render_template("projects.html", projects=projects)


@web_bp.get("/simulations")
@login_required
def simulations_view():
    return render_template("simulations.html")


@web_bp.get("/files")
@login_required
def files_view():
    return render_template("files.html")


@web_bp.get("/tools/calculators")
@login_required
def calculators_view():
    return render_template("calculators.html")


@web_bp.get("/docs")
@login_required
def docs_view():
    return render_template("docs.html")
