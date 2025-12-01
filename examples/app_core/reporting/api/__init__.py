from flask import Blueprint

bp = Blueprint('reporting_api', __name__, url_prefix='/api/reports')

from . import routes  # noqa: F401,E402
