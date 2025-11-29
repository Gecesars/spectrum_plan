from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app.config import get_session
from app.models import Project, User

projects_bp = Blueprint("projects", __name__)


def _get_or_create_default_user(session) -> User:
    user = session.query(User).first()
    if not user:
        user = User(email="user@example.com", password_hash="stub", full_name="User")
        session.add(user)
        session.flush()
    return user


@projects_bp.get("")
def list_projects():
    with get_session() as session:
        projects = session.execute(select(Project)).scalars().all()
        return jsonify(
            [
                {"id": p.id, "name": p.name, "description": p.description, "user_id": p.user_id}
                for p in projects
            ]
        )


@projects_bp.post("")
def create_project():
    data = request.get_json(force=True)
    name = data.get("name")
    if not name:
        return jsonify({"error": "name is required"}), 400

    with get_session() as session:
        user = _get_or_create_default_user(session)
        project = Project(
            name=name,
            description=data.get("description"),
            owner=user,
        )
        session.add(project)
        session.flush()
        return jsonify({"id": project.id, "name": project.name, "user_id": user.id}), 201
