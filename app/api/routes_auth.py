from __future__ import annotations

import secrets
from http import HTTPStatus
from typing import Optional

from flask import Blueprint, jsonify, redirect, render_template, request, url_for, flash
from flask_login import current_user, login_required, login_user, logout_user

from app.config import get_session
from app.models import User
from app.utils.email import send_verification_email, send_password_reset_email

auth_bp = Blueprint("auth", __name__)
auth_api_bp = Blueprint("auth_api", __name__)


def _find_user_by_email(session, email: str) -> Optional[User]:
    return session.query(User).filter(User.email == email).first()


@auth_bp.get("/login")
def login_get():
    if current_user.is_authenticated:
        return redirect(url_for("web.index"))
    return render_template("auth/login.html")


@auth_bp.post("/login")
def login_post():
    data = request.form
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    remember = bool(data.get("remember"))

    with get_session() as session:
        user = _find_user_by_email(session, email)
        if not user or not user.check_password(password):
            flash("Credenciais inválidas", "error")
            return redirect(url_for("auth.login_get"))
        if not user.is_verified:
            flash("Confirme o e-mail enviado para liberar o acesso.", "info")
            return redirect(url_for("auth.login_get"))
        login_user(user, remember=remember)
    return redirect(url_for("web.index"))


@auth_bp.get("/register")
def register_get():
    if current_user.is_authenticated:
        return redirect(url_for("web.index"))
    return render_template("auth/register.html")


@auth_bp.get("/forgot")
def forgot_get():
    if current_user.is_authenticated:
        return redirect(url_for("web.index"))
    return render_template("auth/forgot.html")


@auth_bp.post("/forgot")
def forgot_post():
    email = request.form.get("email", "").strip().lower()
    if not email:
        flash("Informe o e-mail", "error")
        return render_template("auth/forgot.html")
    sent_link = None
    token = secrets.token_urlsafe(24)
    with get_session() as session:
        user = _find_user_by_email(session, email)
        if user:
            user.password_reset_token = token
            session.flush()
            # Envia e também fornece o link na interface quando SMTP não está configurado.
            send_password_reset_email(email, token)
            sent_link = url_for("auth.reset_get", token=token, _external=True)
    flash("Se o e-mail existir, enviamos um link para redefinir a senha.", "info")
    return render_template("auth/forgot.html", reset_link=sent_link)


@auth_bp.get("/reset/<token>")
def reset_get(token: str):
    return render_template("auth/reset.html", token=token)


@auth_bp.post("/reset/<token>")
def reset_post(token: str):
    password = request.form.get("password", "")
    confirm = request.form.get("password_confirm", "")
    from app.models import validate_password_strength

    ok, msg = validate_password_strength(password)
    if not ok:
        flash(msg or "Senha inválida", "error")
        return render_template("auth/reset.html", token=token)
    if password != confirm:
        flash("As senhas não conferem", "error")
        return render_template("auth/reset.html", token=token)

    with get_session() as session:
        user = session.query(User).filter(User.password_reset_token == token).first()
        if not user:
            flash("Token inválido ou expirado", "error")
            return redirect(url_for("auth.forgot_get"))
        user.set_password(password)
        user.password_reset_token = None
        session.flush()
    flash("Senha redefinida. Faça login.", "success")
    return redirect(url_for("auth.login_get"))


@auth_bp.post("/register")
def register_post():
    data = request.form
    full_name = data.get("full_name", "").strip()
    email = data.get("email", "").strip().lower()
    username = data.get("username", "").strip()
    cpf_cnpj = data.get("cpf_cnpj", "").strip()
    company = data.get("company", "").strip()
    address = data.get("address", "").strip()
    phone = data.get("phone", "").strip()
    password = data.get("password", "")
    password_confirm = data.get("password_confirm", "")

    if not full_name or not email or not password:
        flash("Nome completo, email e senha são obrigatórios", "error")
        return render_template("auth/register.html")

    from app.models import validate_password_strength
    ok, msg = validate_password_strength(password)
    if not ok:
        flash(msg or "Senha inválida", "error")
        return render_template("auth/register.html")

    if password != password_confirm:
        flash("As senhas não conferem", "error")
        return render_template("auth/register.html")

    with get_session() as session:
        if _find_user_by_email(session, email):
            flash("Email já cadastrado", "error")
            return render_template("auth/register.html")
        token = secrets.token_urlsafe(24)
        user = User(
            email=email,
            full_name=full_name or email,
            username=username or None,
            cpf_cnpj=cpf_cnpj or None,
            company=company or None,
            address=address or None,
            phone=phone or None,
            is_verified=False,
            verification_token=token,
        )
        try:
            user.set_password(password)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("auth/register.html")
        session.add(user)
        session.flush()
        send_verification_email(email, token)
        flash("Conta criada. Verifique seu e-mail para confirmar.", "success")
    return render_template("auth/register.html", created=True)


@auth_bp.get("/confirm/<token>")
def confirm_account(token: str):
    with get_session() as session:
        user = session.query(User).filter(User.verification_token == token).first()
        if not user:
            flash("Token inválido", "error")
            return redirect(url_for("auth.login_get"))
        user.is_verified = True
        user.verification_token = None
        session.flush()
        flash("Conta verificada. Faça login.", "success")
    return redirect(url_for("auth.login_get"))


@auth_bp.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login_get"))


@auth_api_bp.get("/me")
def api_me():
    if not current_user.is_authenticated:
        return jsonify({"error": "unauthenticated"}), HTTPStatus.UNAUTHORIZED
    return jsonify(
        {
            "id": current_user.id,
            "full_name": current_user.full_name,
            "email": current_user.email,
            "is_admin": current_user.is_admin,
            "is_verified": current_user.is_verified,
            "days_left": 30,
        }
    )
