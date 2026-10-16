"""Mail adapters. One is chosen at boot, by whether SMTP_HOST is set."""

from __future__ import annotations

from ..config import Settings
from .base import Mailer, Message
from .oauth import ClientCredentialsToken, OAuthError
from .outbox import OutboxMailer
from .smtp import SmtpMailer

__all__ = ["ClientCredentialsToken", "Mailer", "Message", "OAuthError",
           "OutboxMailer", "SmtpMailer", "build_mailer"]


def build_mailer(settings: Settings) -> Mailer:
    if not settings.mail_live:
        return OutboxMailer(settings.outbox_dir, settings.mail_from)

    token = None
    if settings.smtp_oauth:
        provider = ClientCredentialsToken(
            settings.smtp_oauth_tenant_id,
            settings.smtp_oauth_client_id,
            settings.smtp_oauth_client_secret,
            settings.smtp_oauth_scope,
            settings.smtp_oauth_authority,
        )
        token = provider.token

    return SmtpMailer(
        settings.smtp_host, settings.smtp_port, settings.smtp_user,
        settings.smtp_password, settings.mail_from, settings.smtp_starttls,
        token=token,
    )
