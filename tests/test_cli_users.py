from __future__ import annotations

from app.models import User
from app.extensions import db


def test_cli_create_user(runner, app):
    result = runner.invoke(
        args=[
            "user.create",
            "--email",
            "cli@test.com",
            "--full-name",
            "CLI User",
            "--password",
            "Strong123",
        ]
    )
    assert result.exit_code == 0
    with app.app_context():
        user = db.session.query(User).filter_by(email="cli@test.com").first()
        assert user is not None
        assert user.check_password("Strong123")
