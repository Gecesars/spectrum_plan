import os
import platform
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate

from extensions import db, login_manager
from user import User
from app_core import models  # noqa: F401
from sqlalchemy import event
from sqlalchemy.engine import Engine


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')

load_dotenv(os.path.join(BASE_DIR, '.env'))


def create_app():
    app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=STATIC_DIR)
    CORS(app)

    # Secrets & configuration
    secret = os.environ.get('SECRET_KEY', 'minha_chave_secreta')
    app.config['SECRET_KEY'] = secret
    app.secret_key = secret
    app.config['database'] = os.environ.get('APP_DATABASE', 'default')

    def _env_bool(name: str, default: bool = False) -> bool:
        return os.environ.get(name, str(default)).lower() in {'1', 'true', 'on', 'yes'}

    # Database configuration
    uri = os.getenv("DATABASE_URL", 'sqlite:///users.db')
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Workaround Windows/psycopg2 UnicodeDecodeError on non-UTF8 server messages:
    # 1) Decode pre-auth messages with LATIN1 to avoid crashes
    # 2) On Windows, force lc_messages=C (ASCII) and client_encoding=UTF8 after connect
    os.environ.setdefault("PGCLIENTENCODING", "LATIN1")
    engine_options = app.config.setdefault('SQLALCHEMY_ENGINE_OPTIONS', {})
    connect_args = engine_options.setdefault("connect_args", {})
    if "options" not in connect_args and platform.system() == "Windows":
        # psycopg2 accepts "options" to send -c switches to the server
        connect_args["options"] = "-c lc_messages=C"

    # Feature flags & security
    app.config['ALLOW_UNCONFIRMED'] = _env_bool('ALLOW_UNCONFIRMED', False)
    app.config['FEATURE_WORKERS'] = _env_bool('FEATURE_WORKERS', False)
    app.config['FEATURE_RT3D'] = _env_bool('FEATURE_RT3D', False)
    app.config['SECURITY_EMAIL_SALT'] = os.environ.get('SECURITY_EMAIL_SALT', 'atx-email-token')
    app.config['EMAIL_CONFIRM_MAX_AGE'] = int(os.environ.get('EMAIL_CONFIRM_MAX_AGE', 60 * 60 * 24))
    app.config['PASSWORD_RESET_MAX_AGE'] = int(os.environ.get('PASSWORD_RESET_MAX_AGE', 60 * 60 * 2))

    # Mail settings
    app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'localhost')
    app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 25))
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
    app.config['MAIL_USE_TLS'] = _env_bool('MAIL_USE_TLS', False)
    app.config['MAIL_USE_SSL'] = _env_bool('MAIL_USE_SSL', False)
    app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER')
    app.config['MAIL_SUPPRESS_SEND'] = _env_bool('MAIL_SUPPRESS_SEND', False)

    app.config['GOOGLE_MAPS_API_KEY'] = os.environ.get('GOOGLE_MAPS_API_KEY')
    app.config['GEMINI_API_KEY'] = os.environ.get('GEMINI_API_KEY')
    app.config['GEMINI_MODEL'] = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
    app.config.setdefault(
        'SOLID_PNG_ROOT',
        os.path.join(STATIC_DIR, 'SOLID_PRT_ASM', 'PNGS'),
    )

    legacy_root = os.environ.get('LEGACY_STORAGE_ROOT') or os.environ.get('STORAGE_ROOT')
    if legacy_root:
        app.config['LEGACY_STORAGE_ROOT'] = legacy_root

    db.init_app(app)
    Migrate(app, db)
    login_manager.init_app(app)
    login_manager.login_view = 'ui.login'
    login_manager.login_message_category = 'warning'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app_core.routes.ui import bp as ui_bp
    app.register_blueprint(ui_bp)
    from app_core.routes.projects import bp as projects_bp, api_bp as projects_api_bp
    app.register_blueprint(projects_bp)
    app.register_blueprint(projects_api_bp)
    from app_core.regulatory.api import bp as regulator_api_bp
    app.register_blueprint(regulator_api_bp)
    from app_core.reporting.api import bp as reporting_api_bp
    app.register_blueprint(reporting_api_bp)

    # Post-connect fix: ensure UTF8 client encoding and ASCII messages
    @event.listens_for(Engine, "connect")
    def _set_pg_encoding(dbapi_connection, connection_record):
        if platform.system() != "Windows":
            return
        try:
            cur = dbapi_connection.cursor()
            # Ensure ASCII messages and UTF8 client encoding after auth
            cur.execute("SET lc_messages TO 'C'")
            cur.execute("SET client_encoding TO 'UTF8'")
            cur.close()
        except Exception:
            # Ignore if not PostgreSQL or not yet available
            try:
                dbapi_connection.rollback()
            except Exception:
                pass

    @app.context_processor
    def inject_defaults():
        return {'current_year': datetime.utcnow().year}

    return app
