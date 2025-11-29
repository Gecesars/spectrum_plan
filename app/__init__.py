from __future__ import annotations

from flask import Flask
from flask_cors import CORS

from app.config import AppConfig, init_db
from app.api import api_bp
from app.web import web_bp


def create_app(config: AppConfig = AppConfig()) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SECRET_KEY"] = config.SECRET_KEY
    CORS(app)

    # Ensure DB schema exists before serving requests.
    init_db()

    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(web_bp)

    return app


app = create_app()
