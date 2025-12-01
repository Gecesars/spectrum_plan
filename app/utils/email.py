from __future__ import annotations

import smtplib
from email.message import EmailMessage

from flask import current_app, url_for, request


def send_verification_email(to_email: str, token: str) -> None:
    """Send verification email using SMTP settings from app config."""
    cfg = current_app.config
    server = cfg.get("MAIL_SERVER")
    username = cfg.get("MAIL_USERNAME")
    password = cfg.get("MAIL_PASSWORD")
    port = cfg.get("MAIL_PORT", 465)
    use_tls = cfg.get("MAIL_USE_TLS", False)
    use_ssl = cfg.get("MAIL_USE_SSL", False)
    sender = cfg.get("MAIL_DEFAULT_SENDER", "noreply@spectrum.local")

    verify_url = url_for("auth.confirm_account", token=token, _external=True)
    msg = EmailMessage()
    msg["Subject"] = "Confirme seu acesso ao Spectrum"
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(
        f"Olá,\n\nConfirme seu acesso ao Spectrum clicando no link abaixo:\n{verify_url}\n\nSe você não solicitou, ignore."
    )

    if not server or not username or not password:
        # Fallback: apenas logar o token se não houver credenciais.
        print(f"[EMAIL MOCK] Destinatário: {to_email} Token: {token} Link: {verify_url}")
        return

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(server, port) as smtp:
                smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(server, port) as smtp:
                if use_tls:
                    smtp.starttls()
                smtp.login(username, password)
                smtp.send_message(msg)
    except Exception as exc:  # pragma: no cover
        print(f"Falha ao enviar e-mail para {to_email}: {exc}")
