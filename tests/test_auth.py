from __future__ import annotations

from app.models import User
from app.extensions import db


def test_login_success(client, app):
    with app.app_context():
        user = User(email="auth@example.com", full_name="Auth User", is_verified=True)
        user.set_password("Strong123")
        db.session.add(user)
        db.session.commit()

    resp = client.post("/auth/login", data={"email": "auth@example.com", "password": "Strong123"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Welcome" in resp.data


def test_login_failure(client):
    resp = client.post("/auth/login", data={"email": "missing@example.com", "password": "bad"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Credenciais inv\xc3\xa1lidas" in resp.data or b"Login" in resp.data
