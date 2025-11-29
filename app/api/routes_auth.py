from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.config import get_session
from app.models import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.get("/me")
def me():
    # Stub: return first user or placeholder.
    with get_session() as session:
        user = session.query(User).first()
    if not user:
        return jsonify({"full_name": "Guest", "email": "guest@example.com", "is_admin": False})
    return jsonify(
        {"full_name": user.full_name or user.email, "email": user.email, "is_admin": False}
    )


@auth_bp.post("/login")
def login():
    # Stub login for Phase 1: echoes payload.
    payload = request.get_json(force=True)
    return jsonify({"message": "Login stub", "payload": payload})
