from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from app.config import get_session
from app.models import Project

projects_bp = Blueprint("projects", __name__)


@projects_bp.get("")
@login_required
def list_projects():
    with get_session() as session:
        projects = session.query(Project).filter(Project.user_id == current_user.id).all()
        return jsonify(
            [
                {"id": p.id, "name": p.name, "description": p.description, "user_id": p.user_id}
                for p in projects
            ]
        )


@projects_bp.post("")
@login_required
def create_project():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    with get_session() as session:
        project = Project(
            name=name,
            description=(data.get("description") or "").strip() or None,
            user_id=current_user.id,
        )
        session.add(project)
        session.flush()
        return (
            jsonify(
                {
                    "id": project.id,
                    "name": project.name,
                    "description": project.description,
                    "user_id": project.user_id,
                }
            ),
            201,
        )
