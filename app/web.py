from __future__ import annotations

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import current_user, login_required

from app.config import get_session
from app.models import Project, User, ProjectArtifact, Simulation

web_bp = Blueprint("web", __name__)


@web_bp.get("/")
def index():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login_get"))
    projects_count = simulations_count = artifacts_count = 0
    with get_session() as session:
        projects_count = session.query(Project).filter(Project.user_id == current_user.id).count()
        # Simulações e artefatos somam globais do usuário via join
        simulations_count = (
            session.query(Project)
            .join(Project.stations)
            .join(Project.simulations, isouter=True)
            .filter(Project.user_id == current_user.id)
            .count()
        )
        artifacts_count = (
            session.query(Project)
            .join(Project.simulations, isouter=True)
            .join(ProjectArtifact, ProjectArtifact.simulation_id == Simulation.id, isouter=True)
            .filter(Project.user_id == current_user.id)
            .count()
        )
    return render_template(
        "home.html",
        welcome_name=current_user.full_name or current_user.email,
        welcome_email=current_user.email,
        welcome_days=30,
        projects_count=projects_count,
        simulations_count=simulations_count,
        artifacts_count=artifacts_count,
    )


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


@web_bp.route("/account", methods=["GET", "POST"])
@login_required
def account_view():
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")
        from app.models import validate_password_strength

        ok, msg = validate_password_strength(password)
        if not ok:
            flash(msg or "Senha inválida", "error")
        elif password != confirm:
            flash("As senhas não conferem", "error")
        else:
            with get_session() as session:
                user = session.get(User, current_user.id)
                if user:
                    user.set_password(password)
                    user.password_reset_token = None
                    session.flush()
            flash("Senha atualizada com sucesso.", "success")
        return redirect(url_for("web.account_view"))
    return render_template("account.html")
