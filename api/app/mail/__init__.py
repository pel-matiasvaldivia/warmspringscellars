"""Mail adapters. One is chosen at boot, by whether SMTP_HOST is set."""

from __future__ import annotations

from ..config import Settings
from .base import Mailer, Message
from .outbox import OutboxMailer
from .smtp import SmtpMailer

__all__ = ["Mailer", "Message", "OutboxMailer", "SmtpMailer", "build_mailer"]


def build_mailer(settings: Settings) -> Mailer:
    if settings.mail_live:
        return SmtpMailer(
            settings.smtp_host, settings.smtp_port, settings.smtp_user,
            settings.smtp_password, settings.mail_from, settings.smtp_starttls,
        )
    return OutboxMailer(settings.outbox_dir, settings.mail_from)
