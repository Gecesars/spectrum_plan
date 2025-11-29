from __future__ import annotations

import datetime as dt

from flask import Blueprint, jsonify
from sqlalchemy import text

from app.config import get_session

debug_bp = Blueprint("debug", __name__)


@debug_bp.get("/health")
def health():
    db_status = "ok"
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
    return jsonify({"status": "ok", "db": db_status, "timestamp": dt.datetime.utcnow().isoformat()})
