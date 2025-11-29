from __future__ import annotations

from flask import Flask

from app.api.routes_auth import auth_bp, auth_api_bp
from app.api.routes_projects import projects_bp
from app.api.routes_debug import debug_bp
from app.api.routes_core import core_bp


def register_api(app: Flask) -> None:
    """Register all API blueprints with their prefixes."""
    app.register_blueprint(debug_bp, url_prefix="/api/debug")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(auth_api_bp, url_prefix="/api/auth")
    app.register_blueprint(projects_bp, url_prefix="/api/projects")
    app.register_blueprint(core_bp, url_prefix="/api")
