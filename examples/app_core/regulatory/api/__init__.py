from flask import Blueprint

bp = Blueprint('regulator_api', __name__, url_prefix='/api/regulator')

from . import routes  # noqa: E402,F401
