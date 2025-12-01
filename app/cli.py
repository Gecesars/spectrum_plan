from __future__ import annotations

import getpass
import click
from flask import Flask

from app.extensions import db
from app.models import User


def register_cli(app: Flask) -> None:
    @app.cli.group("user")
    def user_cmd():
        """User management commands."""

    @user_cmd.command("create")
    @click.option("--email", required=True, help="User email")
    @click.option("--full-name", required=True, help="Full name")
    @click.option("--password", help="Password (prompted if omitted)")
    @click.option("--admin/--no-admin", default=False, help="Admin flag")
    def create_user(email: str, full_name: str, password: str | None, admin: bool):
        """Create a new user with Argon2 password hash."""
        pw = password or getpass.getpass("Password: ")
        if db.session.query(User).filter_by(email=email).first():
            click.echo("User already exists.")
            return
        user = User(email=email.lower(), full_name=full_name, is_admin=admin, is_verified=True)
        user.set_password(pw)
        db.session.add(user)
        db.session.commit()
        click.echo(f"User created with id={user.id}")
    # Alias para permitir `flask user.create ...`
    app.cli.add_command(create_user, "user.create")

    @user_cmd.command("list")
    def list_users():
        """List users."""
        users = db.session.query(User).all()
        for u in users:
            click.echo(
                f"{u.id}: {u.email} | {u.full_name} | admin={u.is_admin} | verified={u.is_verified} | created={u.created_at}"
            )

    @user_cmd.command("set-password")
    @click.option("--email", required=True)
    @click.option("--password", help="New password (prompt if omitted)")
    def set_password(email: str, password: str | None):
        """Set password for a user."""
        user = db.session.query(User).filter_by(email=email.lower()).first()
        if not user:
            click.echo("User not found.")
            return
        pw = password or getpass.getpass("New password: ")
        user.set_password(pw)
        db.session.commit()
        click.echo("Password updated.")

    @user_cmd.command("promote")
    @click.option("--email", required=True)
    def promote(email: str):
        """Promote user to admin."""
        user = db.session.query(User).filter_by(email=email.lower()).first()
        if not user:
            click.echo("User not found.")
            return
        user.is_admin = True
        db.session.commit()
        click.echo("User promoted to admin.")

    # Alias
    app.cli.add_command(user_cmd)
