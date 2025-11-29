from __future__ import annotations

from flask import Flask
from flask_cors import CORS

from app.config import Config, init_db
from app.extensions import init_extensions
from app.api import register_api
from app.web import web_bp
from app.cli import register_cli


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(config_class)
    CORS(app)

    init_extensions(app)
    # Ensure DB schema exists before serving requests (Phase 1 convenience).
    init_db()
    register_api(app)
    app.register_blueprint(web_bp)
    register_cli(app)
    return app
